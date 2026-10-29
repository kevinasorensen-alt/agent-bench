# invoice-extraction-v2: design notes

Not for baseline authors: a non-AI baseline author may read only the task prompt and `sample/`.

- `inputs/`: 40 invoices in 9 layouts. 3 layouts also appear in `sample/`; 6 are held out. Plus 3 non-invoice decoys (quote, receipt, purchase order).
- Held-out traps: German number and date format, OCR noise, part-payment with a smaller balance due and a factoring "remit to" company, US and UK numeric dates, two-page invoice.
- Scoring: 320 points (40 × 8 fields); each decoy included costs 8.

## Baselines (2026-10-28, 3 trials each, identical every trial)

| Baseline | Score | Notes |
|---|---|---|
| Layout-tuned regex (`regex_invoices.py`) | 160/320 (50%) | Written with the v1 layouts in view; includes all 3 decoys |
| Blind script (`blind_invoices_v2.py`) | 280/320 (88%) | Written by a separate AI coding agent from the prompt and `sample/` only (48/48 on the sample); runs with no AI. The spec's header comments mentioned the layout and decoy counts at the time; moved here since |
