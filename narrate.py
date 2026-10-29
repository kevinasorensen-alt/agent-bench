"""Add a spoken commentary track, captions and a 20-second end-screen hold to a recorded bench run.

  runtime/agent-bench/venv/Scripts/python.exe agent-bench/narrate.py runtime/agent-bench/runs/<run id>

Every line is built from the run's own data (timestamps, scores, times, costs), so the voice can only say
what happened. Narration never touches scoring. Output, next to the raw recording:
  <video>-narrated.mp4, <video>.srt (captions) and narration.md (the script, for review).
"""
import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

TTS_DIR = Path.home() / ".cache" / "hyperframes" / "tts"  # Kokoro-82M files, shared with the studio pipeline
VOICE, SPEED = "am_michael", 1.05
END_SCREEN_SECONDS = 20  # YouTube end screens run in the last 5-20 seconds
GAP = 0.35
REPLAY = 16        # trials after the first are shown in full, as a fast replay at this speed
FAST = 8           # quiet stretches (one contestant left, nothing to say) play this much faster
FONT = "C\\:/Windows/Fonts/consolab.ttf"  # escaped for ffmpeg drawtext
MAX_DELAY = {"low": 6, "medium": 15, "high": 1e9}  # drop a line if it would land this late


# --- spoken phrasing ---

def spoken_name(setup):
    name = setup.get("spoken") or setup["name"].replace(" · ", " with ")
    return name.split(" (")[0]


def spoken_time(sec):
    sec = int(round(sec))
    if sec < 90:
        return "1 second" if sec <= 1 else f"{sec} seconds"
    m, s = divmod(sec, 60)
    if m < 5:
        mins = "1 minute" if m == 1 else f"{m} minutes"
        return f"{mins} and {s} seconds" if s else mins
    return f"about {m + (s >= 30)} minutes"


def spoken_cost(usd, setup):
    if setup.get("cost", "none") == "none":
        return "at no cost"
    if usd < 0.005:
        return "on a free tier" if setup.get("prices_usd_per_mtok") else "at no cost"
    if usd < 1:
        return f"for about {max(1, round(usd * 100))} cents"
    return f"for about {usd:.2f} dollars"


def end_of(r):
    """When a contestant stopped. Runs stopped by a cap before the bench logged it fall back to start + seconds."""
    return r.get("ended_at") or (r["started_at"] + r.get("seconds", 0))


def is_baseline(setup):
    return setup.get("network", "none") == "none" and setup.get("cost", "none") == "none"


def thousands(n):
    if n >= 10_000:
        return f"{round(n / 1000)} thousand"
    if n >= 1000:
        return f"{n / 1000:.1f} thousand".replace(".0 ", " ")
    return str(n)


def meter_calls(run_dir, trial, dirname):
    """Model calls the metering proxy saw for one contestant, oldest first."""
    calls = []
    for f in (run_dir / "meter").glob(f"trial-{trial}-{dirname}-*.jsonl"):
        calls += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(calls, key=lambda c: c["t"])


def key_layouts(task):
    """id -> layout from the answer key, and the set of layouts the sample showed (narration only).

    An id is whatever the scorer reports misses by: a file name (v2) or an invoice number (v3).
    """
    tdir = Path(task.get("task_dir", ""))

    def read(path):
        out = {}
        if path.exists():
            with open(path, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    for ident in (r.get("file"), r.get("invoice_number")):
                        if ident:
                            out[ident] = r.get("layout", "")
        return out
    return read(tdir / task["answer_key"]), set(read(tdir / "sample" / "sample_key.csv").values())


def layout_files(task):
    """layout -> the first input file showing it (for the intro cards)."""
    path = Path(task.get("task_dir", "")) / task["answer_key"]
    out = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                first = r.get("file") or (r.get("files") or "").split(";")[0]
                if first and (r.get("layout", "") not in out or first < out[r["layout"]]):
                    out[r.get("layout", "")] = first
    return out


def pct(n, total):
    share = n / total
    return f"{share:.1%}" if 0.995 <= share < 1 else f"{share:.0%}"


def join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# --- building the script from the run ---

def load_run(run_dir):
    spec = json.loads((run_dir / "spec.json").read_text())
    results = json.loads((run_dir / "results.json").read_text())
    rec_file = run_dir / "recording.json"
    if rec_file.exists():
        rec = json.loads(rec_file.read_text())
    else:  # script-only review of an unrecorded run: time zero is two seconds before trial 1
        rec = {"recording_started": results["trials"][0][0]["t0"] - 2, "card_shown": None}
    setups = {s["name"]: s for s in spec["setups"]}
    histories, dirs = {}, {}
    for t, trial in enumerate(results["trials"], start=1):
        for d in (run_dir / f"trial-{t}").iterdir():
            if (d / "result.json").exists():
                name = json.loads((d / "result.json").read_text())["setup"]
                histories[(t, name)] = json.loads((d / "score_history.json").read_text())
                dirs[(t, name)] = d.name
    return spec, results, rec, setups, histories, dirs


def why_slow(run_dir, t, t0, t_rec, ran, setups, histories, dirs):
    """Cues explaining a contestant that takes far longer than the fastest AI setup. Facts from the logs only."""
    ai_done = [r for r in ran if not is_baseline(setups[r["setup"]]) and r["status"] in ("finished", "time cap", "cost cap")]
    if len(ai_done) < 2:
        return [], {}
    if not any(r["status"] == "finished" for r in ai_done):
        return [], {}
    fastest = min((r for r in ai_done if r["status"] == "finished"), key=lambda r: r["seconds"])
    slow = max(ai_done, key=lambda r: r["seconds"])
    if slow["seconds"] < max(120, 3 * fastest["seconds"]):
        return [], {}
    name, cues, finish_note = spoken_name(setups[slow["setup"]]), [], {}
    others_end = max(end_of(r) - t_rec for r in ran if r is not slow)
    parts = [f"While {name} keeps working, here's why it's slow. It's all in the logs."]
    out = slow["tokens"].get("output", 0)
    lean = min((r for r in ai_done if r is not slow and r["tokens"].get("output")),
               key=lambda r: r["tokens"]["output"], default=None)
    if lean and out >= 2 * lean["tokens"]["output"]:
        parts.append(f"It thinks out loud: it generates about {thousands(out)} tokens to write the same file "
                     f"{spoken_name(setups[lean['setup']])} wrote with {thousands(lean['tokens']['output'])}.")
    calls = meter_calls(run_dir, t, dirs.get((t, slow["setup"]), "")) if run_dir else []
    if calls:
        busy, prev = 0.0, slow["started_at"]
        for c in calls:
            busy += c["t"] - (c.get("t_start") or prev)
            prev = c["t"]
        rate = sum(c.get("output", 0) for c in calls) / busy if busy else 0
        if rate:
            parts.append(f"And the endpoint serves it at about {round(rate)} tokens a second, "
                         f"across {len(calls)} calls to the model.")
    if len(parts) > 1:
        cues.append((others_end + 4, "medium", " ".join(parts)))
    elif run_dir and not calls and slow["status"] in ("time cap", "cost cap"):
        cues.append((others_end + 4, "medium", f"{name} is still on its very first reply from the model. "
                     "It's been thinking about how to start, and hasn't written anything yet."))
        finish_note[slow["setup"]] = " It never finished a single reply from the model."
    hist, final = histories.get((t, slow["setup"]), []), slow["correct"]
    reached = min((p["t"] for p in hist if p["correct"] == final and p["t"] > 0), default=None) if final else None
    if reached is not None and slow["seconds"] - reached > 60:
        later = [c for c in calls if (c.get("t_start") or c["t"]) - slow["started_at"] > reached]
        perfect = final == slow["total"]
        more = f"{len(later)} more call{'s' if len(later) != 1 else ''} to the model"
        cues.append((t0 + reached, "medium", f"{name} now has {final} out of {slow['total']}. " + (
            "Everything from here is re-checking a file that's already right" if perfect else
            "That's its final score. The rest is re-checking") +
            (f": {more}, and nothing changes." if later else ".")))
        finish_note[slow["setup"]] = (f" It had all of them after {spoken_time(reached)}." if perfect else
                                      f" Its score stopped moving after {spoken_time(reached)}.")
    return cues, finish_note


def losses(task, r):
    """(points lost per layout, layouts the sample showed, spoken detail of the top three).

    When a contestant left out every invoice of one layout, the detail says so: that's usually a decision
    (it judged them not to be invoices), not a reading mistake.
    """
    layouts, seen = key_layouts(task)
    names = task.get("layout_names") or {}
    lost = Counter(layouts.get(f, "") for f, _ in r.get("misses", []))
    per_layout = Counter(l for l in layouts.values())
    skipped = Counter(layouts.get(f, "") for f in r.get("missing_rows", []))
    parts = []
    for k, v in lost.most_common(3):
        name = names.get(k, k)
        if skipped[k] == per_layout[k] and per_layout[k] > 1:
            parts.append(f"{v} by leaving out all {per_layout[k]} {name}")
        else:
            parts.append(f"{v} on {name}")
    return lost, seen, join(parts) if parts else ""


def the_catch(task, r, setup, total):
    """After the verdict: where a no-AI baseline lost its points, by layout, and which layouts it never saw."""
    lost, seen, detail = losses(task, r)
    lines = [f"Now, the catch with {spoken_name(setup)}. {setup.get('narration', '')}".strip()]
    total_lost = total - r["correct"]
    if total_lost > 0 and lost:
        unseen = sum(v for k, v in lost.items() if k not in seen)
        if seen and unseen == sum(lost.values()):
            lines.append(f"Every point it lost came from layouts it never saw: {detail}.")
        elif seen and unseen:
            lines.append(f"Most of what it lost came from layouts it never saw: {detail}.")
        else:
            lines.append(f"It lost {total_lost} points: {detail}.")
        layouts, _ = key_layouts(task)
        per_layout = Counter(layouts.values())
        whole = {k for k, v in Counter(layouts.get(f, "") for f in r.get("missing_rows", [])).items()
                 if v == per_layout[k] and v > 1}
        others = [f for f in r.get("missing_rows", []) if layouts.get(f, "") not in whole]  # not already named above
        if others:
            n = len(others)
            lines.append(f"It even skipped {'a real invoice' if n == 1 else f'{n} real invoices'}, "
                         "because it didn't recognise them as invoices.")
    if r.get("double_entries"):
        k = len(r["double_entries"])
        lines.append(f"And it entered {k} invoice{'s' if k != 1 else ''} twice: that's how a business pays a supplier twice.")
    lines.append("So a script is fast and free, right up until a supplier sends a layout it has never seen.")
    return " ".join(lines)


def cues_for(spec, results, rec, setups, histories, card_at, dirs=None, run_dir=None):
    task, n_trials = spec["task"], spec["trials"]
    t_rec = rec["recording_started"]
    racers = [s for s in spec["setups"]]
    ai = [spoken_name(s) for s in racers if not is_baseline(s)]
    base = [spoken_name(s) for s in racers if is_baseline(s)]
    intro = [f"Welcome to Agent Bench. Today's job: {task['title'].split(' (')[0].lower()}. " + (task.get("narration") or ""),
             "Here's the exact prompt every setup gets. Each one works in its own sealed container, and the score "
             "comes from code, not from an AI judge."]
    lineup = f"In the race: {join(ai)}." if ai else ""
    if base:
        lineup += (f" And {join(base)}: {'a plain script' if len(base) == 1 else 'plain scripts'} with no AI, "
                   "as the bar to beat.")
    intro.append(lineup.strip())
    cues = [("intro", "high", line) for line in intro if line.strip()]

    for t, trial in enumerate(results["trials"], start=1):
        ran = [r for r in trial if r.get("started_at") and not r["status"].startswith("not run")]
        if not ran:
            continue
        if t > 1:  # shown as a fast replay (see replay_windows)
            t0 = trial[0]["t0"] - t_rec
            cues.append((t0 - 1, "high", f"Trial {t} of {n_trials}, the same race again, at {REPLAY} times speed."))
            scores = ", ".join(f"{spoken_name(setups[r['setup']])} {r['correct']}"
                               for r in sorted(ran, key=lambda r: -r["correct"]))
            cues.append((max(end_of(r) for r in ran) - t_rec, "high", f"Trial {t}: {scores}."))
            continue
        t0 = trial[0]["t0"] - t_rec
        cues.append((t0 + 0.5, "high", f"Trial {t} of {n_trials}. Go." if n_trials > 1 else "Here we go."))
        for r in trial:
            if r["status"].startswith("not run"):
                cues.append((t0 + 1, "medium", f"{spoken_name(setups[r['setup']])} sits this one out: it couldn't start."))
        # first answers and lead changes, from the score histories
        events, leader = [], None
        timeline = sorted({p["t"] for r in ran for p in histories.get((t, r["setup"]), [])})
        firsts = set()
        for ts in timeline:
            now = {r["setup"]: max([p["correct"] for p in histories.get((t, r["setup"]), []) if p["t"] <= ts] or [0]) for r in ran}
            for name, v in now.items():
                if v > 0 and name not in firsts:
                    firsts.add(name)
                    events.append((t0 + ts, "low", f"{spoken_name(setups[name])} has its first answers on the board."))
            top = max(now.values())
            leaders = [k for k, v in now.items() if v == top]
            if top > 0 and len(leaders) == 1 and leaders[0] != leader:
                leader = leaders[0]
                done = next(((end_of(r)) - t_rec for r in ran if r["setup"] == leader), 1e9)
                if t0 + ts < done - 10:
                    events.append((t0 + ts, "medium", f"{spoken_name(setups[leader])} takes the lead with {top}."))
        slow_cues, finish_note = why_slow(run_dir, t, t0, t_rec, ran, setups, histories, dirs or {})
        events += slow_cues
        ai_ends = [end_of(r) - t_rec for r in ran if not is_baseline(setups[r["setup"]])]
        for r in ran:
            s = setups[r["setup"]]
            when = end_of(r) - t_rec
            if is_baseline(s) and t == 1 and r["status"] == "finished" and any(e > when for e in ai_ends):
                events.append((when, "high", f"{spoken_name(s)} is already done: {r['correct']} out of {r['total']}, "
                                             f"in {spoken_time(r['seconds'])}, for free. Remember that number: there's a "
                                             "catch, and we'll come back to it."))
                continue
            if r["status"] in ("time cap", "cost cap"):
                cap_s = task["time_cap_seconds"]
                cap = ((f"the {cap_s // 60}-minute time cap" if cap_s % 60 == 0 else f"the {cap_s}-second time cap")
                       if r["status"] == "time cap" else "the cost cap")
                events.append((when, "high", f"{spoken_name(s)} hits {cap} and is stopped. The file it had written by "
                                             f"then scores {r['correct']} out of {r['total']}, "
                                             f"{spoken_cost(r['cost_usd'], s)}." + finish_note.get(r["setup"], "")))
                continue
            how = {"finished": "is done"}.get(r["status"], "stops")
            events.append((when, "high", f"{spoken_name(s)} {how}: {r['correct']} out of {r['total']}, in "
                                         f"{spoken_time(r['seconds'])}, {spoken_cost(r['cost_usd'], s)}."
                                         + (f" It included {len(r['decoys_included'])} documents that weren't invoices."
                                            if r.get("decoys_included") else "") + finish_note.get(r["setup"], "")))
        # keep the audience company through long waits
        ends = sorted((end_of(r) - t_rec, r["setup"]) for r in ran)
        last_end = ends[-1][0]
        tick = t0 + 75
        quiet = [(c[0] - 10, c[0] + 40) for c in slow_cues]  # the explanation replaces filler around it
        while tick < last_end - 20:
            if any(a <= tick <= b for a, b in quiet):
                tick += 120
                continue
            still = [spoken_name(setups[n]) for e, n in ends if e > tick]
            if still:
                events.append((tick, "low", f"{join(still)} {'is' if len(still) == 1 else 'are'} still working, "
                                            f"{spoken_time(tick - t0)} in."))
            tick += 120
        cues += events

    # verdict over the end card
    summary = results["summary"]
    scored = {n: s for n, s in summary.items() if s.get("trials")}
    if scored:
        best = max(s["correct_mean"] for s in scored.values())
        top = [n for n, s in scored.items() if s["correct_mean"] == best]
        total = next(iter(scored.values()))["total"]
        many = n_trials > 1
        avg = " on average" if many else ""

        def how(n):
            s = scored[n]
            return f"in {spoken_time(s['seconds_mean'])}{avg}, {spoken_cost(s['cost_mean'], setups[n])}"

        def score_of(n):
            s = scored[n]
            if not many:
                return f"{s['correct_mean']:.0f} out of {total}"
            if s["min"] == s["max"]:
                return f"{s['correct_mean']:.0f} out of {total} in {'both' if s['trials'] == 2 else 'all'} {s['trials']} trials"
            return f"an average of {s['correct_mean']:.1f} out of {total} across {s['trials']} trials"

        if len(top) == 1:
            v = f"The winner: {spoken_name(setups[top[0]])}, with {score_of(top[0])}, {how(top[0])}."
        else:
            v = (f"A tie at the top: {join([spoken_name(setups[n]) for n in top])}, "
                 f"{'both' if len(top) == 2 else 'all'} on {score_of(top[0])}.")
            first_trial = {r["setup"]: r for r in results["trials"][0]}
            for n in sorted(top, key=lambda n: scored[n]["seconds_mean"]):
                if first_trial.get(n, {}).get("status") in ("time cap", "cost cap"):
                    v += (f" {spoken_name(setups[n])} got there too, {spoken_cost(scored[n]['cost_mean'], setups[n])}, "
                          "but kept re-checking until the cap stopped it.")
                else:
                    v += f" {spoken_name(setups[n])} did it {how(n)}."
        first = {r["setup"]: r for r in results["trials"][0]}
        for n in sorted(scored, key=lambda n: -scored[n]["correct_mean"]):
            r = first.get(n, {})
            sins = []
            if r.get("made_up"):
                fields = Counter(f.replace("_", " ") for _, f in r["made_up"])
                what, k = fields.most_common(1)[0]
                sins.append(f"made up {len(r['made_up'])} value{'s' if len(r['made_up']) != 1 else ''} that weren't on "
                            f"the documents" + (f", mostly {what}s" if len(fields) > 1 else f": {what}s"))
            if r.get("double_entries"):
                k = len(r["double_entries"])
                sins.append(f"entered {k} invoice{'s' if k != 1 else ''} twice")
            if sins and not is_baseline(setups[n]):
                v += f" {spoken_name(setups[n])} {join(sins)}."
        rest = sorted((n for n in scored if n not in top), key=lambda n: -scored[n]["correct_mean"])
        for n in rest:
            v += f" {spoken_name(setups[n])} scored {pct(scored[n]['correct_mean'], total)}"
            r = first.get(n, {})
            if r and not is_baseline(setups[n]) and r.get("rows_found", 1) == 0 and not r.get("correct"):
                v += ": it didn't get a single row written before it was stopped."
                continue
            detail = losses(task, r)[2] if r and not is_baseline(setups[n]) else ""
            v += f": it lost {detail}." if detail else "."
        if n_trials < 3:
            v += " One trial isn't a verdict, so treat this as a first look."
        cues.append((card_at + 0.5, "high", v))
        for n in scored:
            if is_baseline(setups[n]) and n in first and first[n]["correct"] < total:
                cues.append((card_at + 0.6, "high", the_catch(task, first[n], setups[n], total)))
    intro_cues = [c for c in cues if c[0] == "intro"]
    timed = sorted((c for c in cues if c[0] != "intro"), key=lambda c: c[0])
    cues = intro_cues + timed
    cues.append((None, "high", "Every number you saw came from code. The task, the answer key and the raw logs are "
                               "linked below. Watch the next race on screen now."))
    return cues


# --- speeding up quiet stretches ---

def fast_segments(results, rec, cues, durations):
    """Recording-time windows to play at FAST: all of the processing, except a short stretch around every line
    the voice says about the race (from just before it until it has been said)."""
    t_rec = rec["recording_started"]
    keep = sorted((w - 2, w + d + 1.5) for (w, prio, _), d in zip(cues, durations)
                  if isinstance(w, (int, float)) and prio != "low")
    windows = []
    for trial in results["trials"]:
        ran = [r for r in trial if r.get("started_at") and not r["status"].startswith("not run")]
        if not ran:
            continue
        a, b = trial[0]["t0"] - t_rec + 3, max(end_of(r) for r in ran) - t_rec + 1
        start = a
        for ka, kb in keep:
            if kb < a or ka > b:
                continue
            if ka - start >= 4:
                windows.append((start, ka))
            start = max(start, kb)
        if b - start >= 4:
            windows.append((start, b))
    return windows


def replay_windows(results, rec):
    """Every trial after the first, start to finish, as (start, end, REPLAY): shown in full, just fast."""
    t_rec, out = rec["recording_started"], []
    for trial in results["trials"][1:]:
        ran = [r for r in trial if r.get("started_at")]
        if ran:
            out.append((trial[0]["t0"] - t_rec - 2, max(end_of(r) for r in ran) - t_rec + 1.5, REPLAY))
    return out


def edit_plan(windows, cuts, duration, replays=()):
    """The recording as (start, end, rate) pieces: rate 1 plays normally, FAST is sped up, 0 is cut out, and
    replay windows play at their own rate.

    Cuts (from the run's edits.json, e.g. a desktop popup) win over everything; replays win over speed-ups.
    """
    marks = sorted({0.0, duration, *[x for w in windows for x in w], *[x for c in cuts for x in c],
                    *[x for r in replays for x in r[:2]]})
    plan = []
    for a, b in zip(marks, marks[1:]):
        mid = (a + b) / 2
        replay = next((r[2] for r in replays if r[0] <= mid <= r[1]), None)
        rate = (0 if any(c0 <= mid <= c1 for c0, c1 in cuts) else replay if replay
                else FAST if any(w0 <= mid <= w1 for w0, w1 in windows) else 1)
        if plan and plan[-1][2] == rate:
            plan[-1] = (plan[-1][0], b, rate)
        elif b > a:
            plan.append((a, b, rate))
    return plan


def time_map(plan):
    """Recording time -> edited time under an edit plan."""
    def f(t):
        out = 0.0
        for a, b, rate in plan:
            if t <= a:
                break
            seg = min(t, b) - a
            out += 0 if rate == 0 else seg / rate
        return out
    return f


# --- audio, captions, video ---

def synth_all(cues, out_dir):
    import soundfile as sf
    from kokoro_onnx import Kokoro
    k = Kokoro(str(TTS_DIR / "models" / "kokoro-v1.0.onnx"), str(TTS_DIR / "voices" / "voices-v1.0.bin"))
    out_dir.mkdir(exist_ok=True)
    clips = []
    for i, (_, _, text) in enumerate(cues):
        audio, sr = k.create(text, voice=VOICE, speed=SPEED, lang="en-us")
        path = out_dir / f"{i:02d}.wav"
        sf.write(path, audio, sr)
        clips.append((path, len(audio) / sr))
    return clips


def schedule(cues, clips, card_at, shift):
    """Place lines in order without overlap; drop minor lines that would land too late.

    Intro lines play back to back from 0.5s over the frozen starting grid; every other line moves later by
    `shift`, the length of that freeze.
    """
    placed, free_at = [], 0.0
    for (want, prio, text), (path, dur) in zip(cues, clips):
        if want == "intro":
            want = 0.5
        elif want is None:  # the call to action opens the end-screen stretch
            want = max(free_at, card_at + shift)
        else:
            want += shift
        start = max(want, free_at + (GAP if placed else 0))
        if start - want > MAX_DELAY[prio]:
            continue
        placed.append((start, dur, text, path))
        free_at = start + dur
    return placed


def video_segments(plan):
    """Filter chains for the kept pieces of the recording (normal and sped up), labelled s0, s1, ..."""
    chains = []
    for i, (a, b, speed) in enumerate([p for p in plan if p[2] != 0]):
        label = (f",drawtext=fontfile='{FONT}':text='>> {speed}x speed':x=w-tw-40:y=40:fontsize=44:"
                 "fontcolor=yellow:box=1:boxcolor=black@0.6:boxborderw=14") if speed > 1 else ""
        chains.append(f"[0:v]trim=start={a:.2f}:end={b:.2f},setpts=(PTS-STARTPTS)/{speed},"
                      f"format=yuv420p,setsar=1{label}[s{i}]")
    return chains


def card(path, header, body):
    """A full-screen terminal-style still: a yellow header and a block of monospaced text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    head_txt, body_txt = path.with_suffix(".head.txt"), path.with_suffix(".body.txt")
    head_txt.write_text(header, encoding="utf-8", newline="\n")  # CRLF would render as double spacing
    body_txt.write_text(body, encoding="utf-8", newline="\n")
    esc = lambda q: str(q).replace("\\", "/").replace(":", "\\:")
    vf = (f"drawtext=fontfile='{FONT}':textfile='{esc(head_txt)}':expansion=none:fontsize=40:fontcolor=yellow:x=120:y=50,"
          f"drawtext=fontfile='{FONT.replace('consolab', 'consola')}':textfile='{esc(body_txt)}':expansion=none:"
          "fontsize=28:fontcolor=0xd8d8d8:x=120:y=140:line_spacing=10")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x0c0c0c:s=1920x1080",
                    "-frames:v", "1", "-vf", vf, str(path)], check=True)
    return path


def intro_cards(task, out_dir):
    """(layout cards, prompt card). One example document per layout, as the contestants see it."""
    import textwrap
    files = layout_files(task)
    names = task.get("layout_names") or {}
    order = [k for k in names if k in files]
    tdir = Path(task.get("task_dir", ""))
    cards = []
    for i, layout in enumerate(order, 1):
        f = files[layout]
        text = (tdir / task["inputs"] / f).read_text(encoding="utf-8").replace("\f", "").splitlines()[:30]
        cards.append(card(out_dir / f"layout-{i:02d}.png", f"LAYOUT {i} OF {len(order)}  -  {names[layout]}",
                          "\n".join(line[:110] for line in text)))
    wrapped = "\n".join(textwrap.fill(line, 100) if line.strip() else "" for line in task["prompt"].splitlines())
    prompt = card(out_dir / "prompt.png", "THE PROMPT  -  identical for every setup", wrapped)
    return cards, prompt


def key_rows(task):
    path = Path(task.get("task_dir", "")) / task["answer_key"]
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {(r.get("invoice_number") or r.get("file") or r.get("files") or "").casefold(): r for r in csv.DictReader(f)}


def catch_cards(task, r, setup, out_dir):
    """Up to two evidence cards for a baseline's catch: a real invoice it skipped (preferring a layout it never
    saw), and an invoice it entered twice with both copies. Built from the answer key and the inputs."""
    rows, (_, seen) = key_rows(task), key_layouts(task)
    names = task.get("layout_names") or {}
    tdir = Path(task.get("task_dir", "")) / task["inputs"]
    name = spoken_name(setup).upper()

    def text_of(f, n=24):
        return [line[:110] for line in (tdir / f).read_text(encoding="utf-8").replace("\f", "").splitlines()[:n]]

    def files_of(row):
        return (row.get("files") or row.get("file") or "").split(";")

    cards = []
    skipped = [rows[k.casefold()] for k in r.get("missing_rows", []) if k.casefold() in rows]
    if skipped:
        pick = next((x for x in skipped if x.get("layout") not in seen), skipped[0])
        f = files_of(pick)[0]
        what = names.get(pick.get("layout", ""), "invoices")
        note = " (never shown in its samples)" if seen and pick.get("layout") not in seen else ""
        cards.append(card(out_dir / "catch-skipped.png", f"SKIPPED BY {name}  -  {f}, a real invoice",
                          "\n".join([f"Layout: {what}{note}", ""] + text_of(f))))
    doubles = [rows[k.casefold()] for k in dict.fromkeys(r.get("double_entries", [])) if k.casefold() in rows]
    pair = next((x for x in doubles if len(files_of(x)) > 1), None)
    if pair:
        first, again = files_of(pair)[:2]
        body = text_of(first, 12) + ["", f"-------- the same invoice, sent again: {again} --------", ""] + text_of(again, 12)
        cards.append(card(out_dir / "catch-twice.png",
                          f"ENTERED TWICE BY {name}  -  invoice {pair['invoice_number']}", "\n".join(body)))
    return cards


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def main(run_dir, script_only=False):
    run_dir = Path(run_dir)
    spec, results, rec, setups, histories, dirs = load_run(run_dir)
    if script_only:
        last = max(end_of(r) for tr in results["trials"] for r in tr if r.get("started_at")) - rec["recording_started"]
        cues = [c for c in cues_for(spec, results, rec, setups, histories, last + 3, dirs, run_dir) if c[2].strip()]
        clips = [(None, len(c[2].split()) / 2.7) for c in cues]  # speaking-time estimate; no audio made
        shift = 0.5 + sum(d + GAP for (w, _, _), (_, d) in zip(cues, clips) if w == "intro") + 0.5
        placed = schedule(cues, clips, last + 3, shift)
        text = "\n".join(f"- **{int(s // 60):02d}:{s % 60:04.1f}** {t}" for s, _, t, _ in placed)
        (run_dir / "narration-draft.md").write_text("# Narration draft (estimated timing)\n\n" + text + "\n",
                                                    encoding="utf-8")
        print(text)
        return
    video = Path(rec["video"])
    duration = probe_duration(video)
    # bench.py logs when the results card appeared and stops recording while it's still up
    card_at = (rec["card_shown"] - rec["recording_started"]) if rec.get("card_shown") else duration - 4
    # Filler ("still working", "first answers") is left out: the sped-up footage and its label carry those.
    cues = [c for c in cues_for(spec, results, rec, setups, histories, card_at, dirs, run_dir)
            if c[2].strip() and c[1] != "low"]
    clips = synth_all(cues, run_dir / "narration")
    windows = fast_segments(results, rec, cues, [d for _, d in clips])
    edits = json.loads((run_dir / "edits.json").read_text()) if (run_dir / "edits.json").exists() else {}
    cuts = [tuple(c["range"]) for c in edits.get("cut", [])]
    plan = edit_plan(windows, cuts, duration, replay_windows(results, rec))
    f = time_map(plan)
    cues = [((f(w) if isinstance(w, (int, float)) else w), p_, t_) for w, p_, t_ in cues]
    edited_card_at, edited_duration = f(card_at), f(duration)

    # Intro: the layouts flash by under line 1, the prompt sits under line 2, the starting grid under the lineup.
    intro_d = [d for (w, _, _), (_, d) in zip(cues, clips) if w == "intro"]
    segs = [0.5 + intro_d[0] + GAP] + [d + GAP for d in intro_d[1:-1]] + [intro_d[-1] + 0.5]
    shift = sum(segs)
    placed = schedule(cues, clips, edited_card_at, shift)

    # The end card lasts through the verdict, then a full END_SCREEN_SECONDS for YouTube end-screen elements.
    cta_start = placed[-1][0]
    final = max(edited_duration + shift, cta_start + END_SCREEN_SECONDS)
    pad = final - edited_duration - shift

    (run_dir / "narration.md").write_text("# Narration\n\n" + "\n".join(
        f"- **{int(s // 60):02d}:{s % 60:04.1f}** {text}" for s, _, text, _ in placed) +
        f"\n\nEnd screen: {final - END_SCREEN_SECONDS:.1f}s to {final:.1f}s ({END_SCREEN_SECONDS}s).\n", encoding="utf-8")
    video.with_suffix(".srt").write_text("\n".join(
        f"{i}\n{srt_time(s)} --> {srt_time(s + d)}\n{text}\n" for i, (s, d, text, _) in enumerate(placed, 1)),
        encoding="utf-8")

    # Starting grid: the race screen the bench shows 1.5s before anything starts.
    grid_at = max(0.0, results["trials"][0][0]["t0"] - rec["recording_started"] - 0.7)
    grid = run_dir / "narration" / "grid.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{grid_at:.2f}", "-i", str(video), "-frames:v", "1", str(grid)], check=True)
    layout_cards, prompt_card = intro_cards(spec["task"], run_dir / "narration" / "cards")
    stills = ([(c, segs[0] / len(layout_cards)) for c in layout_cards] if layout_cards else [(grid, segs[0])])
    stills += [(prompt_card, sum(segs[1:-1]))] if len(segs) > 2 else []
    stills += [(grid, segs[-1])]

    race_end = shift + edited_duration  # where the recording (ending on the results card) runs out
    end_card = run_dir / "narration" / "end-card.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.3", "-i", str(video), "-frames:v", "1", str(end_card)],
                   check=True)
    catch = next(((st, du) for st, du, text, _ in placed if text.startswith("Now, the catch")), None)
    evidence = []
    if catch:
        first = {r["setup"]: r for r in results["trials"][0]}
        base = next((n for n in first if is_baseline(setups[n])), None)
        if base:
            evidence = catch_cards(spec["task"], first[base], setups[base], run_dir / "narration" / "cards")
    tail = []
    if evidence and catch[0] >= race_end:
        each = catch[1] / len(evidence)
        tail = [(end_card, catch[0] - race_end)] + [(e, each) for e in evidence] + [(end_card, final - catch[0] - catch[1])]
    elif final > race_end:
        tail = [(end_card, final - race_end)]
    tail = [(img, d) for img, d in tail if d > 0.05]
    n_intro = len(stills)
    stills += tail

    kept = [p for p in plan if p[2] != 0]
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video)]
    for img, d in stills:
        cmd += ["-loop", "1", "-framerate", "30", "-t", f"{d:.3f}", "-i", str(img)]
    first_audio = len(stills) + 1
    for _, _, _, path in placed:
        cmd += ["-i", str(path)]
    parts = [f"[{i}:a]adelay={int(s * 1000)}:all=1[a{i}]" for i, (s, _, _, _) in enumerate(placed, first_audio)]
    mix = "".join(f"[a{i}]" for i in range(first_audio, first_audio + len(placed)))
    still_chains = [f"[{i}:v]format=yuv420p,setsar=1,fps=30[st{i}]" for i in range(1, len(stills) + 1)]
    graph = ";".join(parts + [f"{mix}amix=inputs={len(placed)}:normalize=0:duration=longest,apad,"
                              f"loudnorm=I=-16:TP=-1.5:LRA=11[aout]"] + still_chains + video_segments(plan) +
                     [f"{''.join(f'[st{i}]' for i in range(1, n_intro + 1))}{''.join(f'[s{i}]' for i in range(len(kept)))}"
                      f"{''.join(f'[st{i}]' for i in range(n_intro + 1, len(stills) + 1))}"
                      f"concat=n={len(stills) + len(kept)}:v=1:a=0[vout]"])
    out = video.with_name(video.stem + "-narrated.mp4")
    cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", "-t", f"{final:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(out)]
    subprocess.run(cmd, check=True)
    print(f"{out}\n{len(placed)} lines spoken, {len(cues) - len(placed)} dropped; "
          f"{len(windows)} stretch(es) at {FAST}x and {len(cuts)} cut(s), saving {duration - edited_duration:.0f}s; "
          f"{len(evidence)} evidence card(s); {final:.0f}s long, "
          f"end screen from {final - END_SCREEN_SECONDS:.0f}s")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--script-only"]
    if len(args) != 1:
        sys.exit(__doc__ + "\n  --script-only  print the script with estimated timing; no audio or video")
    main(args[0], script_only="--script-only" in sys.argv)
