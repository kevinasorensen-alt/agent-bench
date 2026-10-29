"""Open a full-screen Windows Terminal titled "Agent Bench", run the bench live inside it, and record it.

  runtime/agent-bench/venv/Scripts/python.exe agent-bench/record.py tasks/invoice-extraction-v2 setups/baseline-blind.yaml ... --trials 1

The video lands in runtime/agent-bench/videos/<time>-<task>.mp4. Only the Agent Bench window is recorded
(Windows Graphics Capture), so popups and other windows never appear in it, and the machine stays usable
while it runs. Don't minimise the window: a minimised window stops sending frames.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEOS = HERE.parent / "runtime" / "agent-bench" / "videos"
TITLE = "Agent Bench"


def main(args):
    if not args or args[0].startswith("-"):
        sys.exit(__doc__)
    out = VIDEOS / f"{datetime.now():%Y%m%d-%H%M%S}-{Path(args[0]).name}.mp4"
    cmd = ["wt", "-w", "new", "--fullscreen", "--title", TITLE, "--suppressApplicationTitle",
           "-d", str(HERE.parent), sys.executable, str(HERE / "bench.py"), *args,
           "--record", str(out), "--window-title", TITLE]
    subprocess.run(cmd, check=True)
    print(f"Recording to {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
