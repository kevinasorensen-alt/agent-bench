"""Code-only scorers. No AI anywhere in a score.

Each scorer takes (output_path, answer_key_path) and returns a dict with at least
correct, total and detail. A missing or unreadable output scores 0, never an error.
"""
import csv
import re
from pathlib import Path

INVOICE_FIELDS = ["invoice_number", "vendor", "invoice_date", "due_date", "currency", "subtotal", "tax", "total"]


def _norm(field, value):
    v = (value or "").strip()
    if field in ("subtotal", "tax", "total"):
        v = re.sub(r"[^\d.\-]", "", v)
        try:
            x = round(float(v), 2)
            return f"{0.0 if x == 0 else x:.2f}"  # -0.00 and 0.00 are the same amount
        except ValueError:
            return None
    if field in ("vendor", "currency", "invoice_number", "doc_type", "needs_review"):
        # Case and spacing aren't the test; the value is.
        return " ".join(v.split()).casefold()
    return v


def score_invoices(output_path, key_path):
    """Fields correct out of (invoices × 8). Key rows with is_invoice=no are decoys: each one that shows up
    in the output costs 8 points (one invoice's worth), floored at 0. That rule is part of the task spec."""
    with open(key_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    key = {r["file"]: r for r in rows if r.get("is_invoice", "yes") != "no"}
    decoys = {r["file"] for r in rows if r.get("is_invoice") == "no"}
    total = len(key) * len(INVOICE_FIELDS)
    got, decoys_included = {}, set()
    try:
        with open(output_path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = (r.get("file") or "").strip()
                if name in key and name not in got:  # first row per file counts; duplicates can't win twice
                    got[name] = r
                elif name in decoys:
                    decoys_included.add(name)
    except (OSError, csv.Error, UnicodeDecodeError):
        pass
    per_field = {f: 0 for f in INVOICE_FIELDS}
    misses = []  # [file, field] for every point lost; narration groups these by layout
    for name, want in key.items():
        row = got.get(name) or {}
        for f in INVOICE_FIELDS:
            if row and _norm(f, row.get(f)) is not None and _norm(f, row.get(f)) == _norm(f, want[f]):
                per_field[f] += 1
            else:
                misses.append([name, f])
    fields_correct = sum(per_field.values())
    penalty = len(decoys_included) * len(INVOICE_FIELDS)
    return {"correct": max(0, fields_correct - penalty), "total": total, "fields_correct": fields_correct,
            "decoys_included": sorted(decoys_included), "rows_found": len(got), "detail": per_field,
            "misses": misses, "missing_rows": sorted(set(key) - set(got))}


MONTH_END_FIELDS = ["vendor", "doc_type", "invoice_date", "due_date", "currency", "subtotal", "tax", "total", "needs_review"]


def score_month_end(output_path, key_path):
    """Month-end close: one row per invoice or credit note, matched by invoice number (so a resent invoice can't
    score twice). 9 points per document. An empty answer-key field must stay empty: filling it is a made-up value.
    Each extra row for an invoice already listed (a double entry) and each row from a decoy file costs 9 points.
    These rules are part of the task spec."""
    with open(key_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    key = {_norm("invoice_number", r["invoice_number"]): r for r in rows if r["trap"] != "decoy"}
    decoy_files = {r["files"] for r in rows if r["trap"] == "decoy"}
    total = len(key) * len(MONTH_END_FIELDS)
    got, doubles, decoys_included, unknown = {}, [], set(), []
    try:
        with open(output_path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                num = _norm("invoice_number", r.get("invoice_number"))
                if num in key:
                    if num in got:
                        doubles.append(num)
                    else:
                        got[num] = r
                elif (r.get("file") or "").strip() in decoy_files:
                    decoys_included.add(r["file"].strip())
                elif num:
                    unknown.append(num)
    except (OSError, csv.Error, UnicodeDecodeError):
        pass
    per_field = {f: 0 for f in MONTH_END_FIELDS}
    misses, made_up = [], []
    for num, want in key.items():
        row = got.get(num) or {}
        for f in MONTH_END_FIELDS:
            have, expect = (row.get(f) or "").strip(), want[f].strip()
            if not expect:
                ok = bool(row) and not have
                if row and have:
                    made_up.append([want["invoice_number"], f])
            else:
                ok = bool(row) and _norm(f, have) is not None and _norm(f, have) == _norm(f, expect)
            if ok:
                per_field[f] += 1
            else:
                misses.append([want["invoice_number"], f])
    fields_correct = sum(per_field.values())
    penalty = (len(doubles) + len(decoys_included)) * len(MONTH_END_FIELDS)
    return {"correct": max(0, fields_correct - penalty), "total": total, "fields_correct": fields_correct,
            "decoys_included": sorted(decoys_included), "double_entries": doubles, "made_up": made_up,
            "unknown_rows": unknown, "rows_found": len(got), "detail": per_field, "misses": misses,
            "missing_rows": sorted(key[n]["invoice_number"] for n in set(key) - set(got))}


SCORERS = {"invoices": score_invoices, "month_end": score_month_end}


def score(name, output_path, key_path):
    return SCORERS[name](Path(output_path), Path(key_path))
