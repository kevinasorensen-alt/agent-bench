"""Re-score a finished run with the current scorer and answer key, without re-running anything.

  runtime/agent-bench/venv/Scripts/python.exe agent-bench/rescore.py runtime/agent-bench/runs/<run id> "reason"

For fixing a scoring or answer-key bug. Each contestant's saved output.csv is scored again; result.json files,
results.json and results.md are rewritten, and every change (old score, new score, reason) is appended to
rescore.json in the run folder, so a corrected result is never silently different from what was first shown.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

import bench
import scorers

KEEP = ["correct", "total", "detail", "decoys_included", "misses", "missing_rows", "made_up", "double_entries",
        "unknown_rows", "rows_found"]


def main(run_dir, reason):
    run_dir = Path(run_dir)
    spec = json.loads((run_dir / "spec.json").read_text())
    task = spec["task"]
    task["dir"] = Path(task["task_dir"])
    results = json.loads((run_dir / "results.json").read_text())
    changes = []
    for t, trial in enumerate(results["trials"], start=1):
        for r in trial:
            d = next(d for d in (run_dir / f"trial-{t}").iterdir()
                     if (d / "result.json").exists() and json.loads((d / "result.json").read_text())["setup"] == r["setup"])
            if r["status"].startswith("not run"):
                continue
            new = scorers.score(task["scorer"], d / "work" / task["output"], task["dir"] / task["answer_key"])
            if new["correct"] != r["correct"]:
                changes.append({"trial": t, "setup": r["setup"], "old": r["correct"], "new": new["correct"]})
            r.update({k: new.get(k, r.get(k)) for k in KEEP})
            saved = json.loads((d / "result.json").read_text())
            saved.update({k: new.get(k, saved.get(k)) for k in KEEP})
            (d / "result.json").write_text(json.dumps(saved, indent=1))
    trials = [[{k: v for k, v in r.items()} for r in tr] for tr in results["trials"]]
    bench.summarise(task, trials, run_dir)
    log = run_dir / "rescore.json"
    history = json.loads(log.read_text()) if log.exists() else []
    history.append({"when": datetime.now().isoformat(timespec="seconds"), "reason": reason, "changes": changes})
    log.write_text(json.dumps(history, indent=1))
    for c in changes:
        print(f"trial {c['trial']} {c['setup']}: {c['old']} -> {c['new']}")
    print(f"{len(changes)} change(s); logged in {log}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
