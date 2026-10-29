"""Baseline contestant with no AI: plain regular expressions over the invoice text.

Real baseline for episodes ("does AI beat a 50-line script?"). With --delay/--drop it doubles as a
smoke-test dummy that fills the scoreboard slowly; that mode never appears in a published result.
"""
import argparse
import csv
import random
import re
import time
from datetime import datetime
from pathlib import Path

SYM = {"$": "USD", "€": "EUR", "£": "GBP"}


def date_iso(s):
    s = s.strip().rstrip(".")
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def num(s):
    return f"{float(s.replace(',', '')):.2f}" if s else ""


def find(pattern, text):
    m = re.search(pattern, text, re.M)
    return m.group(1).strip() if m else ""


def extract(text):
    row = {"invoice_number": find(r"\b((?:INV|IN|BW|NO)-\d+)", text)}
    if text.startswith("Subject:"):
        row["vendor"] = find(r"from (.+)$", text.splitlines()[0])
        row["invoice_date"] = date_iso(find(r"dated (\w+ \d+, \d{4})", text))
        row["due_date"] = date_iso(find(r"due by (\w+ \d+, \d{4})", text))
        row["currency"] = find(r"total of ([A-Z]{3})", text)
        row["total"] = num(find(r"total of [A-Z]{3} ([\d,]+\.\d\d)", text))
        row["subtotal"] = num(find(r"net ([\d,]+\.\d\d)", text))
        row["tax"] = num(find(r"tax ([\d,]+\.\d\d)", text))
    elif "|  Tax invoice" in text:
        row["vendor"] = find(r"^(.+?)\s+\|", text)
        row["invoice_date"] = date_iso(find(r"Issued (\d+ \w+ \d{4})", text))
        row["due_date"] = date_iso(find(r"Payment due (\d+ \w+ \d{4})", text))
        row["currency"] = find(r"Amount \(([A-Z]{3})\)", text)
        row["subtotal"] = num(find(r"^Net\s+([\d,]+\.\d\d)", text))
        row["tax"] = num(find(r"^VAT/Tax\s+([\d,]+\.\d\d)", text))
        row["total"] = num(find(r"^Amount due\s+([\d,]+\.\d\d)", text))
    else:
        row["vendor"] = text.splitlines()[0].strip()  # upper-cased in this layout; the scorer ignores case
        row["invoice_date"] = date_iso(find(r"^Date: (.+)$", text))
        row["due_date"] = date_iso(find(r"^Due: (.+)$", text))
        row["currency"] = SYM.get(find(r"TOTAL: (\S)", text), "")
        row["subtotal"] = num(find(r"Subtotal: \D([\d,]+\.\d\d)", text))
        row["tax"] = num(find(r"Tax \([^)]*\): \D([\d,]+\.\d\d)", text))
        row["total"] = num(find(r"TOTAL: \D([\d,]+\.\d\d)", text))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", default="/work/inputs")
    ap.add_argument("--out", default="/work/output.csv")
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--drop", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    fields = ["file", "invoice_number", "vendor", "invoice_date", "due_date", "currency", "subtotal", "tax", "total"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for p in sorted(Path(a.inputs).glob("*.txt")):
            row = {"file": p.name, **extract(p.read_text(encoding="utf-8"))}
            if a.drop:
                row = {k: ("" if k != "file" and rng.random() < a.drop else v) for k, v in row.items()}
            w.writerow(row)
            f.flush()
            print(f"read {p.name}: {row['invoice_number'] or '?'} {row['total'] or '?'} {row['currency']}", flush=True)
            time.sleep(a.delay)
    print("done", flush=True)


if __name__ == "__main__":
    main()
