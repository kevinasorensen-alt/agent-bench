# Agent Bench

Race AI setups side by side on the same real job, live on screen, **scored only by code**. No AI judges.

Every contestant gets the same prompt and the same input files in its own throwaway Docker container. The
answer key never enters a container. A live scoreboard re-scores each contestant's output every two seconds,
and every run keeps its raw logs, score history and output files.

## Episode 1: month-end close (2026-10-28)

138 fictional supplier documents: invoices in nine layouts, credit notes, account statements, invoices sent
twice, and paperwork that isn't an invoice (quotes, purchase orders, receipts, remittance advice). Traps:
invoices with no due date (filling one in is a made-up value), totals that don't add up (flag them), credit
notes printed with positive amounts. 124 documents x 9 fields = **1,116 points**. 3 trials each.

| Setup | Score (every trial) | Avg time | Avg cost (API-equivalent) |
|---|---|---|---|
| Claude Code 2.1.312 + Claude Opus 5.5 | 1,116 / 1,116 | 87 s | $0.71 |
| Codex CLI 0.156.1 + GPT-6 Astra (medium reasoning) | 1,116 / 1,116 | 87 s | $0.70 |
| Plain script, no AI ([`baselines/blind_month_end_v3.py`](baselines/blind_month_end_v3.py)) | 925 / 1,116 | 1 s | free |

The script skipped 9 real invoices and entered 5 invoices twice. It was written by an AI coding agent that
saw only the prompt and the 8 files in [`tasks/month-end-close-v3/sample/`](tasks/month-end-close-v3/sample/),
then runs with no AI at all. Both AI setups ran on subscription plans; costs are what the same tokens would
cost through the API.

**Raw logs of that run:** [`runs/2026-10-28-month-end-close-v3/`](runs/2026-10-28-month-end-close-v3/): every
contestant's agent log, output file, score history and result, for all three trials.

**Correction before publishing:** the first version of the answer key wrote `-0.00` as the tax of two
zero-tax credit notes, so a correct `0.00` scored as wrong. The key and scorer were fixed, earlier runs were
re-scored with [`rescore.py`](rescore.py) (which logs every change), and the race was re-recorded. Details in
[`tasks/month-end-close-v3/NOTES.md`](tasks/month-end-close-v3/NOTES.md).

## How a run works

```
task spec (tasks/<task>/task.yaml) + answer key (stays on the host)
        |
bench.py: for each trial, every setup in its own container (2 GB, 2 CPUs, no network unless the setup needs it)
        |
scorers.py: code only. Fields vs the answer key; made-up values, double entries and decoys tracked separately
        |
live scoreboard  ->  results.md / results.json + raw logs per trial
```

- **Fairness:** same prompt, same files, fresh container per trial, several trials, every trial reported.
  Non-AI baselines are written blind from the public sample only.
- **Cost:** Claude Code reports its own API-equivalent cost; Codex reports tokens (priced with list prices
  pinned in the setup); tools that report nothing go through [`meter.py`](meter.py), a local proxy that counts
  every token and adds the provider key itself, so the key never enters a container.
- **Logins:** API keys are passed by name only. Plan logins are copied per trial and deleted after; the Claude
  copy holds only the access token (no refresh token), so a container can't change the host's login.

## Tasks

| Task | Documents | Points | Notes |
|---|---|---|---|
| [`month-end-close-v3`](tasks/month-end-close-v3/) | 138 | 1,116 | Episode 1 |
| [`invoice-extraction-v2`](tasks/invoice-extraction-v2/) | 43 | 320 | Pilot; too easy for frontier models |
| [`invoice-extraction`](tasks/invoice-extraction/) | 20 | 160 | Smoke test only |

Each task folder has its generator (seeded, byte-identical rebuild), inputs, answer key, a public sample, and
notes. Baseline authors may read only the prompt and `sample/`.

## Run it

Needs Docker, Python 3.11+, and the contestant CLIs' logins or keys.

```
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # (bin/ on macOS/Linux)
docker pull python:3.12-slim
docker build -t agent-bench/claude-code:2.1.312 --build-arg CLAUDE_CODE_VERSION=2.1.312 -f docker/claude-code.Dockerfile docker
docker build -t agent-bench/codex:0.156.1 --build-arg CODEX_VERSION=0.156.1 -f docker/codex.Dockerfile docker
python bench.py tasks/month-end-close-v3 setups/baseline-blind-v3.yaml setups/claude-code.yaml setups/codex.yaml --trials 3
```

Results land in `runtime/agent-bench/runs/<run id>/` next to the checkout. `--no-live` prints plain progress
lines. Tests: `python tests/test_bench.py`.

Video tooling (Windows): [`record.py`](record.py) records only the bench window (Windows Graphics Capture via
ffmpeg); [`narrate.py`](narrate.py) builds the voiceover from the run's data (local Kokoro TTS), speeds up
waiting stretches with an on-screen label, and adds captions; [`short.py`](short.py) cuts a portrait version.

## License

MIT. See [LICENSE](LICENSE).
