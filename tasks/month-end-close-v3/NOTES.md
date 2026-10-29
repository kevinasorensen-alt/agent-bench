# month-end-close-v3: design notes

Not for baseline authors: a non-AI baseline author may read only the task prompt and `sample/`.

- `inputs/`: 138 files. 110 single invoices in 9 layouts (3 also in `sample/`, 6 held out), 8 credit notes,
  2 account statements listing 3 invoices each, 8 resends of earlier invoices (a "RESENT" email or a
  "DUPLICATE COPY" stamp), 10 decoys (3 quotes, 3 purchase orders, 2 payment receipts, 2 remittance advices
  that name a real invoice number).
- Traps, each stated in the prompt: 10 invoices with no due date anywhere and 8 credit notes with none
  (an answer there is a made-up value); 6 invoices whose printed total doesn't equal subtotal + tax
  (copy as printed, needs_review = yes); credit notes printed with positive amounts (answer negative).
- Scoring (`month_end`): 124 documents x 9 fields = 1116 points, matched by invoice number. Each double
  entry or decoy row costs 9. Made-up values are listed separately for narration.
- `answer_key.csv`: `files` lists every file containing that document (original first, then resends);
  `trap` names the trap; decoy rows have an empty invoice number.

## Fixes before publication

- 2026-10-28: the answer key wrote `-0.00` as the tax of two zero-tax credit notes (CN-9603, CR-27405), so a
  correct `0.00` scored as wrong (2 points). Key now writes `0.00`; the scorer treats `-0.00` and `0.00` as
  equal. Inputs unchanged. Today's two v3 runs were re-scored with `rescore.py` (logged in each run's
  `rescore.json`): Claude Code and Codex moved to 1116/1116 in every trial. The recorded race was re-shot,
  because its on-screen scoreboard showed the old numbers.
