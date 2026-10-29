"""Cut a 60-90 second LinkedIn version of a recorded run: 4:5 portrait, captions burned in (feeds autoplay muted).

  runtime/agent-bench/venv/Scripts/python.exe agent-bench/short.py runtime/agent-bench/runs/<run id>

Run narrate.py first (it makes the intro and evidence cards this reuses). Like narrate.py, every line is
built from the run's own data. Output: <video>-linkedin.mp4 and <video>-linkedin.srt next to the recording.
"""
import json
import subprocess
import sys
from pathlib import Path

import narrate as N

W, H = 1080, 1350          # 4:5 portrait
FRAME_H = 608              # the 16:9 footage, scaled to the full width
FRAME_Y = 170


def title_card(path, lines):
    """Big centred text on the terminal background (the hook)."""
    txt = path.with_suffix(".txt")
    txt.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    esc = str(txt).replace("\\", "/").replace(":", "\\:")
    vf = (f"drawtext=fontfile='{N.FONT}':textfile='{esc}':expansion=none:fontsize=76:fontcolor=yellow:"
          "line_spacing=24:x=(w-tw)/2:y=(h-th)/2")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x0c0c0c:s=1920x1080",
                    "-frames:v", "1", "-vf", vf, str(path)], check=True)
    return path


def script(spec, results):
    """The five spoken lines, from the run's results."""
    setups = {s["name"]: s for s in spec["setups"]}
    summary = {n: s for n, s in results["summary"].items() if s.get("trials")}
    ai = [n for n in summary if not N.is_baseline(setups[n])]
    base = [n for n in summary if N.is_baseline(setups[n])]
    task = spec["task"]
    total = next(iter(summary.values()))["total"]
    trials = max(s["trials"] for s in summary.values())
    names = N.join([N.spoken_name(setups[n]) for n in ai])
    best = max(summary[n]["correct_mean"] for n in ai)
    perfect = [n for n in ai if summary[n]["min"] == total]
    who = [N.spoken_name(setups[n]).split(" with ")[0] for n in ai] + (["a free script"] if base else [])
    hook = f"We gave {N.join(who)} the same job: {task['title'].split(' (')[0].lower()}."
    setup = (f"{len(list((Path(task['task_dir']) / task['inputs']).glob('*.txt')))} documents: invoices in nine layouts, "
             "credit notes, statements, invoices sent twice, and traps. Scored by code against an answer key.")
    if perfect and len(perfect) == len(ai):
        race = (f"{names}: every one of the {total} fields right, in all {trials} trials. "
                f"About {N.spoken_time(sum(summary[n]['seconds_mean'] for n in ai) / len(ai))} and "
                f"{N.spoken_cost(sum(summary[n]['cost_mean'] for n in ai) / len(ai), setups[ai[0]]).replace('for ', '')} each.")
    else:
        lead = max(ai, key=lambda n: summary[n]["correct_mean"])
        race = (f"Best: {N.spoken_name(setups[lead])}, with {best:.0f} of {total} fields right on average, "
                f"in {N.spoken_time(summary[lead]['seconds_mean'])}.")
    catch = ""
    if base:
        b = summary[base[0]]
        r = next(x for x in results["trials"][0] if x["setup"] == base[0])
        catch = f"The free script took {N.spoken_time(b['seconds_mean'])} and scored {N.pct(b['correct_mean'], total)}. But it"
        bits = []
        if r.get("missing_rows"):
            bits.append(f"skipped {len(r['missing_rows'])} real invoices")
        if r.get("double_entries"):
            bits.append(f"entered {len(r['double_entries'])} twice")
        catch += " " + N.join(bits) + "." if bits else " missed the layouts it had never seen."
        if r.get("double_entries"):
            catch += " That's how a business pays a supplier twice."
    close = "Every number came from code. The full race, the task and the raw logs are on YouTube."
    return [hook, setup, race, catch, close]


def main(run_dir):
    run_dir = Path(run_dir).resolve()
    spec, results, rec, setups, histories, dirs = N.load_run(run_dir)
    video = Path(rec["video"])
    out_dir = run_dir / "linkedin"
    out_dir.mkdir(exist_ok=True)
    lines = [l for l in script(spec, results)]
    clips = N.synth_all([(None, "high", l) for l in lines], out_dir / "voice")
    d = [dur + 0.4 for _, dur in clips]

    cards_dir = run_dir / "narration" / "cards"
    layouts = sorted(cards_dir.glob("layout-*.png"))
    evidence = [p for p in (cards_dir / "catch-skipped.png", cards_dir / "catch-twice.png") if p.exists()]
    end_card = run_dir / "narration" / "end-card.png"
    if not layouts or not end_card.exists():
        sys.exit("Run narrate.py on this run first: it makes the cards this cut reuses.")
    n_ai = sum(1 for s in spec["setups"] if not N.is_baseline(s))
    has_base = any(N.is_baseline(s) for s in spec["setups"])
    n_docs = len(list((Path(spec["task"]["task_dir"]) / spec["task"]["inputs"]).glob("*.txt")))
    hook = title_card(out_dir / "hook.png", [f"{n_ai} AI agents" + (" vs a free script" if has_base else ""),
                                              f"{n_docs} documents", "scored by code"])

    # Pieces: hook | layouts flash | trial 1 at whatever speed fits the line | evidence | results card
    t_rec = rec["recording_started"]
    trial = results["trials"][0]
    a = trial[0]["t0"] - t_rec - 0.5
    b = max(N.end_of(r) for r in trial if r.get("started_at")) - t_rec + 1
    speed = max(1.0, (b - a) / d[2])
    stills = [(hook, d[0])] + [(c, d[1] / len(layouts)) for c in layouts]
    tail = [(e, d[3] / len(evidence)) for e in evidence] if evidence and lines[3] else []
    tail += [(end_card, d[4] + (0 if tail else d[3]) + 1.5)]
    final = sum(d) + 1.5

    starts, t = [], 0.0
    for dur in d:
        starts.append(t)
        t += dur
    N.Path(video.with_name(video.stem + "-linkedin.srt")).write_text("\n".join(
        f"{i}\n{N.srt_time(s)} --> {N.srt_time(s + dur - 0.4)}\n{text}\n"
        for i, (s, dur, text) in enumerate(zip(starts, d, lines), 1) if text), encoding="utf-8")

    srt = out_dir / "captions.srt"
    srt.write_text(video.with_name(video.stem + "-linkedin.srt").read_text(encoding="utf-8"), encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video)]
    for img, dur in stills + tail:
        cmd += ["-loop", "1", "-framerate", "30", "-t", f"{dur:.3f}", "-i", str(img)]
    first_audio = 1 + len(stills) + len(tail)
    for path, _ in clips:
        cmd += ["-i", str(path)]
    n_st = len(stills)
    chains = [f"[{i}:v]format=yuv420p,setsar=1,fps=30[p{i}]" for i in range(1, first_audio)]
    chains.append(f"[0:v]trim=start={a:.2f}:end={b:.2f},setpts=(PTS-STARTPTS)/{speed:.3f},fps=30,format=yuv420p,setsar=1,"
                  f"drawtext=fontfile='{N.FONT}':text='>> {speed:.0f}x speed':x=w-tw-40:y=40:fontsize=48:"
                  "fontcolor=yellow:box=1:boxcolor=black@0.6:boxborderw=14[race]")
    order = "".join(f"[p{i}]" for i in range(1, n_st + 1)) + "[race]" + "".join(
        f"[p{i}]" for i in range(n_st + 1, first_audio))
    chains.append(f"{order}concat=n={first_audio}:v=1:a=0,scale={W}:{FRAME_H},"
                  f"pad={W}:{H}:0:{FRAME_Y}:color=0x0c0c0c,"
                  f"drawtext=fontfile='{N.FONT}':text='AGENT BENCH':x=(w-tw)/2:y=60:fontsize=56:fontcolor=0x5fd7ff,"
                  "subtitles=captions.srt:force_style='FontName=Consolas,FontSize=15,PrimaryColour=&H00FFFFFF,"
                  "BorderStyle=3,OutlineColour=&H80000000,Outline=1,Shadow=0,Alignment=2,MarginV=40,MarginL=20,MarginR=20'"
                  "[vout]")
    audio = [f"[{i}:a]adelay={int(s * 1000)}:all=1[a{i}]" for i, s in zip(range(first_audio, first_audio + len(clips)), starts)]
    mix = "".join(f"[a{i}]" for i in range(first_audio, first_audio + len(clips)))
    audio.append(f"{mix}amix=inputs={len(clips)}:normalize=0:duration=longest,apad,loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    out = video.with_name(video.stem + "-linkedin.mp4")
    cmd += ["-filter_complex", ";".join(chains + audio), "-map", "[vout]", "-map", "[aout]", "-t", f"{final:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(out)]
    subprocess.run(cmd, check=True, cwd=out_dir)  # cwd: the subtitles filter reads captions.srt by relative path
    print(f"{out}\n{final:.0f}s, race at {speed:.0f}x\n" + "\n".join(f"- {l}" for l in lines if l))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
