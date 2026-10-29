"""Agent Bench controller: race setups side by side on one task, live, scored only by code.

  python agent-bench/bench.py tasks/invoice-extraction setups/baseline-regex.yaml setups/dummy-slow.yaml --trials 1

Every setup runs in its own throwaway container with only the task inputs mounted. The answer key never
leaves the host. The live screen re-scores each contestant's output every couple of seconds. Raw logs,
score history and results go to runtime/agent-bench/runs/<run id>/.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import yaml
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import scorers
from meter import Meter, load_dotenv

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RUNS = REPO / "runtime" / "agent-bench" / "runs"
SECRETS_DIR = REPO / "runtime" / "agent-bench" / "secrets"  # per-trial copies of login files, deleted after
SCORE_EVERY = 2.0


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def tree_hash(folder):
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(folder).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


# --- what a contestant's raw output looks like on screen, and what it cost ---

TOKEN_COST_KINDS = {"codex-json"}  # these report tokens; the setup must pin prices so the cost cap works
METERED_KINDS = {"metered-proxy"}  # the bench measures tokens itself, through meter.py
COST_KINDS = {"none", "claude-stream-json"} | TOKEN_COST_KINDS | METERED_KINDS


def view_line(kind, raw):
    """Turn one raw output line into (text for the pane or None, cost event or None).

    A cost event is ("total_usd", x) when the tool reports its running dollar total, or
    ("tokens", {"input", "cached_input", "output"}) when it reports usage for one turn.
    """
    if kind == "none":
        return raw, None
    try:
        ev = json.loads(raw)
    except ValueError:
        return raw, None
    if not isinstance(ev, dict):
        return raw, None
    if kind == "claude-stream-json":
        if ev.get("type") == "result":
            cost, u = ev.get("total_cost_usd"), ev.get("usage") or {}
            cached = (u.get("cache_read_input_tokens") or 0)
            tokens = {"input": (u.get("input_tokens") or 0) + cached + (u.get("cache_creation_input_tokens") or 0),
                      "cached_input": cached, "output": u.get("output_tokens") or 0}
            return f"[finished: {ev.get('subtype', '')}]", (("total_usd", cost, tokens) if cost is not None else None)
        if ev.get("type") == "assistant":
            parts = []
            for c in ev.get("message", {}).get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    parts.append(c["text"].strip())
                elif c.get("type") == "tool_use":
                    parts.append(f"→ {c.get('name')} {json.dumps(c.get('input', {}))[:80]}")
            return (" | ".join(parts) or None), None
        return None, None
    if kind == "codex-json":
        if ev.get("type") == "turn.completed":
            u = ev.get("usage", {})
            return None, ("tokens", {"input": u.get("input_tokens", 0), "cached_input": u.get("cached_input_tokens", 0),
                                     "output": u.get("output_tokens", 0)})
        if ev.get("type") == "turn.failed":
            return f"[turn failed: {ev.get('error', {}).get('message', '')}]", None
        item = ev.get("item", {}) if ev.get("type") == "item.completed" else {}
        if item.get("type") == "agent_message":
            return item.get("text", "").strip() or None, None
        if item.get("type") == "command_execution":
            return f"→ $ {item.get('command', '')[:100]}", None
        if item.get("type") == "file_change":
            return "→ edit " + ", ".join(ch.get("path", "?") for ch in item.get("changes", [])), None
        return None, None
    return raw, None


def token_cost(usage, prices):
    """USD for one turn. Cached tokens are a subset of input tokens and bill at the cached rate."""
    cached = usage.get("cached_input", 0)
    fresh = max(0, usage.get("input", 0) - cached)
    return (fresh * prices["input"] + cached * prices.get("cached_input", prices["input"])
            + usage.get("output", 0) * prices["output"]) / 1_000_000


def _dig(obj, dotted):
    for part in dotted.split("."):
        obj = obj[part]
    return obj


def login_seconds_left(f):
    """Seconds until the login in a secret file expires (expires_at is a dotted JSON path, in ms)."""
    return _dig(json.loads(Path(f["from"]).expanduser().read_text(encoding="utf-8")), f["expires_at"]) / 1000 - time.time()


def copy_login(f, dest):
    """Copy a login file for a container. With `keep`, write only those JSON keys: e.g. Claude's access token
    without its refresh token (so the container can never rotate the host's login) and without any other
    logins stored in the same file. Values are never printed."""
    src = Path(f["from"]).expanduser()
    if not f.get("keep"):
        shutil.copy2(src, dest)
        return
    data = json.loads(src.read_text(encoding="utf-8"))
    out = {section: {k: data[section][k] for k in keys if k in data.get(section, {})} for section, keys in f["keep"].items()}
    dest.write_text(json.dumps(out), encoding="utf-8")


class Contestant:
    def __init__(self, setup, setup_path, task, trial_dir, secrets=None, meters=None):
        self.setup, self.task = setup, task
        self.secrets, self.meters = secrets or {}, meters or {}
        self.meter_file, self.meter_pos, self.secret_dir = None, 0, None
        self.name = setup["name"]
        self.dir = trial_dir / slug(Path(setup_path).stem)
        self.work = self.dir / "work"
        self.lines = deque(maxlen=200)
        self.cost = 0.0
        self.tokens = {}
        self.score = {"correct": 0, "total": 0}
        self.history = []
        self.status = "waiting"
        self.started = self.ended = None
        self.proc = None
        self.cname = f"ab-{slug(self.name)}-{int(time.time() * 1000) % 10**8}"

    def start(self, prompt):
        missing = [k for k in self.setup.get("env", []) if not self.secrets.get(k)]
        prices = self.setup.get("prices_usd_per_mtok") or {}
        kind = self.setup.get("cost", "none")
        if kind in TOKEN_COST_KINDS | METERED_KINDS and (prices.get("input") is None or prices.get("output") is None):
            missing.append("prices_usd_per_mtok in the setup file")
        if kind in METERED_KINDS and self.setup.get("upstream") not in self.meters:
            missing.append(f"a metering proxy for upstream '{self.setup.get('upstream')}'")
        for f in self.setup.get("secret_files", []):
            if not Path(f["from"]).expanduser().is_file():
                missing.append(f"login file {f['from']}")
            elif f.get("expires_at"):
                left = login_seconds_left(f) - self.task["time_cap_seconds"] - 300
                if left < 0:
                    missing.append(f"a fresher login ({f['from']} expires before the time cap; "
                                   "use the tool once on this machine to renew it)")
        if kind not in COST_KINDS:
            missing.append(f"a cost meter ('{kind}' isn't supported yet)")
        elif kind == "none" and self.setup.get("network", "none") != "none":
            missing.append("a cost meter (a setup with network access can't show $0.00)")
        self.dir.mkdir(parents=True)
        shutil.copytree(self.task["dir"] / self.task["inputs"], self.work / "inputs")
        self.inputs_hash = tree_hash(self.work / "inputs")
        self.log = open(self.dir / "agent.log", "w", encoding="utf-8")
        if missing:
            self.status = f"not run: missing {', '.join(missing)}"
            self.started = self.ended = time.time()
            return
        cmd = ["docker", "run", "--rm", "--name", self.cname,
               "--network", self.setup.get("network", "none"),
               "--memory", "2g", "--cpus", "2", "--pids-limit", "512",
               "-v", f"{self.work}:/work", "-v", f"{HERE / 'baselines'}:/bench-tools:ro", "-w", "/work",
               "-e", "BENCH_PROMPT"]
        env = {**os.environ, "BENCH_PROMPT": prompt}
        for k in self.setup.get("env", []):
            cmd += ["-e", k]  # by name only, so values never appear in the command line or logs
            env[k] = self.secrets[k]
        if kind in METERED_KINDS:
            meter = self.meters[self.setup["upstream"]]
            cid = f"{self.dir.parent.name}-{self.dir.name}-{int(time.time())}"
            self.meter_file = meter.dir / f"{cid}.jsonl"
            env["BENCH_API_BASE"] = meter.url_for(cid)
            cmd += ["-e", "BENCH_API_BASE", "--add-host", "host.docker.internal:host-gateway"]
        if self.setup.get("secret_files"):
            # Fresh copies of login files per trial, mounted as the tool's config folder and deleted afterwards,
            # so a contestant can never change the host's own login.
            self.secret_dir = SECRETS_DIR / self.cname
            for f in self.setup["secret_files"]:
                dest = self.secret_dir / slug(f["to_dir"])
                dest.mkdir(parents=True, exist_ok=True)
                copy_login(f, dest / Path(f["from"]).name)
            for f in {f["to_dir"] for f in self.setup["secret_files"]}:
                cmd += ["-v", f"{self.secret_dir / slug(f)}:{f}"]
        cmd += [self.setup["image"], "sh", "-c", self.setup["command"]]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1)
        self.started = time.time()
        self.status = "running"
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            raw = raw.rstrip("\n")
            self.log.write(raw + "\n")
            self.log.flush()
            shown, cost = view_line(self.setup.get("cost", "none"), raw)
            if cost and cost[0] == "total_usd":
                self.cost = float(cost[1])
                if len(cost) > 2:
                    self.tokens = dict(cost[2])
            elif cost and cost[0] == "tokens":
                self.cost += token_cost(cost[1], self.setup["prices_usd_per_mtok"])
                for k, n in cost[1].items():
                    self.tokens[k] = self.tokens.get(k, 0) + n
            if shown:
                self.lines.append(shown)

    def stop(self, why):
        subprocess.run(["docker", "kill", self.cname], capture_output=True)
        self.status = why
        self.ended = time.time()

    def read_meter(self):
        """New usage lines from the metering proxy: add their cost, and show each API call in the pane."""
        if not self.meter_file or not self.meter_file.exists():
            return
        with open(self.meter_file, encoding="utf-8") as f:
            f.seek(self.meter_pos)
            new = f.read()
            self.meter_pos = f.tell()
        for line in new.splitlines():
            try:
                u = json.loads(line)
            except ValueError:
                continue
            usage = {k: u.get(k, 0) for k in ("input", "cached_input", "output")}
            self.cost += token_cost(usage, self.setup["prices_usd_per_mtok"])
            for k, n in usage.items():
                self.tokens[k] = self.tokens.get(k, 0) + n
            self.lines.append(f"[api {u.get('status')}] {u.get('model', '')}: {usage['input']} in, {usage['output']} out")

    def poll(self, now, cap_s, cap_usd):
        self.read_meter()
        if self.status != "running":
            return
        rc = self.proc.poll()
        if rc is not None:
            self.ended = now
            self.status = "finished" if rc == 0 else f"exit {rc}"
        elif now - self.started > cap_s:
            self.stop("time cap")
        elif self.cost > cap_usd:
            self.stop("cost cap")

    def rescore(self, now, t0):
        self.score = scorers.score(self.task["scorer"], self.work / self.task["output"], self.task["dir"] / self.task["answer_key"])
        self.history.append({"t": round(now - t0, 1), "correct": self.score["correct"], "cost": round(self.cost, 4)})

    def elapsed(self, now):
        if not self.started:
            return 0.0
        return (self.ended or now) - self.started

    def result(self, now):
        self.log.close()
        self.read_meter()
        if self.secret_dir:
            shutil.rmtree(self.secret_dir, ignore_errors=True)
        extra = sorted(p.relative_to(self.work).as_posix() for p in self.work.rglob("*")
                       if p.is_file() and not p.relative_to(self.work).as_posix().startswith("inputs/")
                       and p.name != self.task["output"])
        res = {"setup": self.name, "status": self.status, "correct": self.score["correct"],
               "total": self.score["total"], "detail": self.score.get("detail"),
               "decoys_included": self.score.get("decoys_included", []), "misses": self.score.get("misses", []),
               "missing_rows": self.score.get("missing_rows", []), "made_up": self.score.get("made_up", []),
               "double_entries": self.score.get("double_entries", []), "unknown_rows": self.score.get("unknown_rows", []),
               "rows_found": self.score.get("rows_found"),
               "cost_usd": round(self.cost, 4), "tokens": self.tokens,
               "seconds": round(self.elapsed(now), 1),
               "inputs_modified": tree_hash(self.work / "inputs") != self.inputs_hash, "extra_files": extra}
        (self.dir / "score_history.json").write_text(json.dumps(self.history, indent=1))
        (self.dir / "result.json").write_text(json.dumps(res, indent=1))
        return res


# --- the live screen ---

def tokens_label(tokens):
    n = tokens.get("input", 0) + tokens.get("output", 0)
    return "-" if not n else f"{n / 1000:.1f}k" if n < 1_000_000 else f"{n / 1_000_000:.2f}M"


def bar(frac, width=20):
    n = round(frac * width)
    return "█" * n + "░" * (width - n)


def screen(task, trial, trials, contestants, now, t0, pane_lines):
    mins, secs = divmod(int(now - t0), 60)
    head = Text.assemble(("AGENT BENCH  ", "bold cyan"), (task["title"], "bold"),
                         f"   trial {trial} of {trials}   ", (f"{mins:02d}:{secs:02d} real time", "bold yellow"),
                         ("   scored by code, no AI judge", "dim"))
    panes = Layout()
    panes.split_row(*[Layout(Panel(Text("\n".join(list(c.lines)[-pane_lines:]), overflow="ellipsis", no_wrap=True),
                                   title=c.name, border_style="cyan")) for c in contestants])
    board = Table(expand=True, header_style="bold")
    for col, kw in [("Setup", {}), ("Correct", {"justify": "right"}), ("", {}), ("Tokens", {"justify": "right"}),
                    ("Cost (API equiv.)", {"justify": "right"}), ("Time", {"justify": "right"}), ("Status", {})]:
        board.add_column(col, **kw)
    lead = max((c.score["correct"] for c in contestants), default=0)
    for c in contestants:
        total = c.score["total"] or 1
        style = "bold green" if lead and c.score["correct"] == lead else ""
        board.add_row(c.name, f"{c.score['correct']}/{c.score['total']}", bar(c.score["correct"] / total),
                      tokens_label(c.tokens), f"${c.cost:.2f}", f"{c.elapsed(now):.0f}s", c.status, style=style)
    root = Layout()
    root.split_column(Layout(Panel(head), size=3), Layout(panes, ratio=1),
                      Layout(Panel(board, title="SCOREBOARD", border_style="yellow"), size=len(contestants) + 6))
    return root


def run_trial(task, setups, trial, trials, trial_dir, console, live_mode, secrets=None, meters=None):
    contestants = [Contestant(s, p, task, trial_dir, secrets, meters) for p, s in setups]
    pane_lines = max(5, console.size.height - len(contestants) - 14)
    t0 = time.time()
    for c in contestants:
        c.rescore(t0, t0)  # fills in the totals so the starting grid reads 0/320, not 0/0
    if live_mode:
        # The starting grid: everyone on zero before anything starts. narrate.py freezes this frame for the intro.
        with Live(screen(task, trial, trials, contestants, t0, t0, pane_lines), console=console, screen=True):
            time.sleep(1.5)
    t0 = time.time()
    for c in contestants:
        c.start(task["prompt"])
    last_score = 0.0

    def tick():
        nonlocal last_score
        now = time.time()
        for c in contestants:
            c.poll(now, task["time_cap_seconds"], task["cost_cap_usd"])
        if now - last_score >= SCORE_EVERY:
            for c in contestants:
                c.rescore(now, t0)
            last_score = now
        return now

    def running():
        return any(c.status == "running" for c in contestants)

    if live_mode:
        with Live(screen(task, trial, trials, contestants, t0, t0, pane_lines), console=console,
                  refresh_per_second=4, screen=True) as live:
            while running():
                now = tick()
                live.update(screen(task, trial, trials, contestants, now, t0, pane_lines))
                time.sleep(0.25)
            time.sleep(1)  # let the reader threads drain
            now = time.time()
            for c in contestants:
                c.rescore(now, t0)
            live.update(screen(task, trial, trials, contestants, now, t0, pane_lines))
            time.sleep(3)  # hold the final board on screen for the recording
    else:
        last_print = 0.0
        while running():
            now = tick()
            if now - last_print >= 5:
                console.print(f"[{now - t0:5.0f}s] " + "  ".join(
                    f"{c.name}: {c.score['correct']}/{c.score['total']} ${c.cost:.2f} {c.status}" for c in contestants))
                last_print = now
            time.sleep(0.25)
        time.sleep(1)
        now = time.time()
        for c in contestants:
            c.rescore(now, t0)
    # Wall-clock times let narrate.py line the voiceover up with the recording.
    return [dict(c.result(time.time()), t0=t0, started_at=c.started, ended_at=c.ended) for c in contestants]


def summarise(task, results, run_dir):
    by_setup = {}
    for trial in results:
        for r in trial:
            by_setup.setdefault(r["setup"], []).append(r)
    lines = [f"# {task['title']} ({task['id']})", "", f"Run {run_dir.name}. Scored by code only.", "",
             "| Setup | Trials | Correct (mean) | Range | Cost per trial (mean) | Seconds (mean) | Flags |",
             "|---|---|---|---|---|---|---|"]
    summary = {}
    for name, all_rs in by_setup.items():
        # A setup the harness never started is a harness problem, not a zero score.
        rs = [r for r in all_rs if not r["status"].startswith("not run")]
        if not rs:
            summary[name] = {"trials": 0, "flags": sorted({r["status"] for r in all_rs})}
            lines.append(f"| {name} | 0 | - | - | - | - | {', '.join(summary[name]['flags'])} |")
            continue
        c = [r["correct"] for r in rs]
        flags = sorted({r["status"] for r in rs if r["status"] != "finished"}
                       | ({"inputs modified"} if any(r["inputs_modified"] for r in rs) else set()))
        total = rs[0]["total"]
        summary[name] = {"trials": len(rs), "correct_mean": statistics.mean(c), "min": min(c), "max": max(c),
                         "total": total, "cost_mean": statistics.mean(r["cost_usd"] for r in rs),
                         "seconds_mean": statistics.mean(r["seconds"] for r in rs), "flags": flags}
        s = summary[name]
        lines.append(f"| {name} | {s['trials']} | {s['correct_mean']:.1f}/{total} ({s['correct_mean'] / total:.0%}) | "
                     f"{s['min']}-{s['max']} | ${s['cost_mean']:.2f} | {s['seconds_mean']:.0f} | {', '.join(flags) or '-'} |")
    (run_dir / "results.json").write_text(json.dumps({"task": task["id"], "trials": results, "summary": summary}, indent=1))
    (run_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines)


def results_card(task, summary):
    """Full-screen end card: results in the top part, the rest left clear for YouTube end-screen elements."""
    table = Table(expand=True, header_style="bold")
    for col, kw in [("Setup", {}), ("Correct", {"justify": "right"}), ("Range", {"justify": "right"}),
                    ("Avg time", {"justify": "right"}), ("Cost (API equiv.)", {"justify": "right"})]:
        table.add_column(col, **kw)
    ranked = sorted(summary.items(), key=lambda kv: (-kv[1].get("correct_mean", -1), kv[1].get("seconds_mean", 1e9)))
    for i, (name, s) in enumerate(ranked):
        if not s.get("trials"):
            table.add_row(name, "did not run", "", "", "", style="dim")
            continue
        table.add_row(name, f"{s['correct_mean']:.0f}/{s['total']}  ({s['correct_mean'] / s['total']:.0%})",
                      f"{s['min']}-{s['max']}", f"{s['seconds_mean']:.0f}s", f"${s['cost_mean']:.2f}",
                      style="bold green" if i == 0 else "")
    trials = max((s.get("trials", 0) for s in summary.values()), default=0)
    head = Text.assemble(("AGENT BENCH  ", "bold cyan"), ("RESULTS  ", "bold yellow"), (task["title"], "bold"),
                         (f"   {trials} trial{'s' if trials != 1 else ''} each · scored by code, no AI judge", "dim"))
    root = Layout()
    root.split_column(Layout(Panel(Group(head, Text(""), table), border_style="yellow"), ratio=2),
                      Layout(Text("\nFull results, raw logs and the task: link in the description",
                                  justify="center", style="dim"), ratio=3))
    return root


def start_recording(path, window_title=None, monitor=0):
    """Record to an mp4 with ffmpeg. With a window title, Windows Graphics Capture records only that window, so
    popups and other windows on top never reach the video, and the machine stays usable (just don't minimise it).
    Otherwise the whole monitor (Desktop Duplication). Window capture of Windows Terminal via gdigrab is black,
    hence these two. Returns the ffmpeg process, or None if ffmpeg isn't installed.
    """
    if not shutil.which("ffmpeg"):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    if window_title:
        source = f"gfxcapture=window_title='^{window_title}$':capture_cursor=0:max_framerate=30"
    else:
        source = f"ddagrab=output_idx={monitor}:framerate=30:draw_mouse=0"
    return subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", source,
                             "-vf", "hwdownload,format=bgra,fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2",
                             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=open(path.with_suffix(".ffmpeg.log"), "w"))


def stop_recording(proc):
    if proc:
        try:
            proc.communicate(b"q", timeout=30)  # "q" lets ffmpeg finish the file cleanly
        except subprocess.TimeoutExpired:
            proc.kill()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", help="task folder, e.g. tasks/invoice-extraction")
    ap.add_argument("setups", nargs="+", help="setup YAML files")
    ap.add_argument("--trials", type=int, help="overrides the task's trial count")
    ap.add_argument("--no-live", action="store_true", help="plain progress lines instead of the live screen")
    ap.add_argument("--record", metavar="MP4", help="record the whole monitor to this file (use record.py)")
    ap.add_argument("--monitor", type=int, default=0, help="monitor to record when no --window-title (0 = first)")
    ap.add_argument("--window-title", help="record only the window with this exact title (record.py sets it)")
    a = ap.parse_args(argv)

    task_dir = (HERE / a.task) if not Path(a.task).is_absolute() else Path(a.task)
    task = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))
    task["dir"] = task_dir
    setups = []
    for p in a.setups:
        sp = (HERE / p) if not Path(p).is_absolute() else Path(p)
        setups.append((sp, yaml.safe_load(sp.read_text(encoding="utf-8"))))
    trials = a.trials or task.get("trials", 3)

    run_dir = RUNS / f"{datetime.now():%Y%m%d-%H%M%S}-{slug(task['id'])}"
    run_dir.mkdir(parents=True)
    (run_dir / "spec.json").write_text(json.dumps({"task": {**{k: v for k, v in task.items() if k != "dir"}, "task_dir": str(task["dir"])},
                                                   "setups": [s for _, s in setups], "trials": trials}, indent=1))
    console = Console()
    live_mode = console.is_terminal and not a.no_live
    # Keys come from the shell, the repo .env and the station's Hermes .env (where the NVIDIA key lives).
    # They're handed to containers by name only and never printed.
    secrets = {**load_dotenv(REPO / ".hermes-home" / ".env"), **load_dotenv(REPO / ".env"), **os.environ}
    config = REPO / "config" / "models.json"  # the station's provider list; a standalone checkout uses the default
    providers = (json.loads(config.read_text(encoding="utf-8"))["providers"] if config.exists() else
                 {"nvidia": {"baseUrl": "https://integrate.api.nvidia.com/v1", "keyEnv": "NVIDIA_API_KEY"}})
    meters = {}
    for _, s in setups:
        up = s.get("upstream")
        if s.get("cost") in METERED_KINDS and up in providers and up not in meters:
            key = secrets.get(providers[up].get("keyEnv") or "", "")
            if key:
                meters[up] = Meter(providers[up]["baseUrl"], key, run_dir / "meter").start()
    recorder = start_recording(Path(a.record), a.window_title, a.monitor) if a.record else None
    recording_started = time.time()
    results = []
    try:
        for t in range(1, trials + 1):
            results.append(run_trial(task, setups, t, trials, run_dir / f"trial-{t}", console, live_mode, secrets, meters))
        text = summarise(task, results, run_dir)
        card_shown = None
        if live_mode:
            summary = json.loads((run_dir / "results.json").read_text())["summary"]
            with Live(results_card(task, summary), console=console, screen=True):
                time.sleep(0.5)
                card_shown = time.time()
                time.sleep(4 if recorder else 8)
                # Stop while the card is still up: narrate.py freezes the last frame as the end card.
                stop_recording(recorder)
                recorder = None
        console.print(text)
        console.print(f"\nRaw logs and results: {run_dir}")
    finally:
        stop_recording(recorder)
        for m in meters.values():
            m.stop()
    if a.record:
        (run_dir / "recording.json").write_text(json.dumps({"video": a.record, "recording_started": recording_started,
                                                            "card_shown": card_shown}))
    (run_dir / "done").write_text(str(a.record or ""))


if __name__ == "__main__":
    sys.exit(main())
