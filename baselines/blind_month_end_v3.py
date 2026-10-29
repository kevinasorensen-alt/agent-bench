"""Non-AI baseline for the month-end-close-v3 task.

Written blind: the author saw only the task prompt and the sample/ folder (8 sample
documents + sample_key.csv), nothing else from the task. Plain Python, standard library,
no network, no LLM. Rules are label/keyword based so they can generalise to unseen layouts.

Usage: python blind_month_end_v3.py [--inputs /work/inputs] [--out /work/output.csv]
"""
import argparse
import csv
import os
import re

HEADER = ["file", "invoice_number", "vendor", "doc_type", "invoice_date", "due_date",
          "currency", "subtotal", "tax", "total", "needs_review"]

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "oct": 10, "nov": 11, "dec": 12, "janvier": 1, "januar": 1, "februar": 2,
          "fevrier": 2, "février": 2, "mars": 3, "märz": 3, "maerz": 3, "avril": 4, "mai": 5,
          "juin": 6, "juni": 6, "juillet": 7, "juli": 7, "août": 8, "aout": 8, "oktober": 10,
          "octobre": 10, "dezember": 12, "décembre": 12, "decembre": 12, "okt": 10, "dez": 12,
          "sept": 9}

# Amount with exactly two decimals, any common thousands separator; not a % and not part of a date.
AMOUNT = re.compile(r"(?<![\w.,/-])(-\s?|\()?(\d{1,3}(?:[,. ' ]\d{3})+|\d+)([.,])(\d{2})(?![\d%])(?![.,]\d)(\))?")

DOC_KEYWORDS = [  # (regex, kind) - the earliest hit near the top of the document decides its type
    (r"credit\s*(?:note|memo)|gutschrift|\bavoir\b", "credit_note"),
    (r"statement", "statement"),
    (r"\bquot(?:e|ation)\b|\bestimate\b|\bangebot\b|\bdevis\b", "skip"),
    (r"purchase\s+order(?!\s*(?:no|number|#|ref|:))|order\s+confirmation", "skip"),
    (r"remittance|payment\s+(?:receipt|confirmation|advice|for\s+invoice|of\s+invoice|received)"
     r"|\breceipt\b|thank\s+you\s+for\s+your\s+payment|pro[\s-]?forma", "skip"),
    (r"invoice|rechnung|facture|\bbill\b", "invoice"),
]


def to_number(m):
    neg, whole, _sep, dec, close = m.groups()
    value = float(re.sub(r"\D", "", whole) + "." + dec)
    return -value if (neg and neg.startswith("-")) or (neg == "(" and close) else value


def parse_date(s, us):
    """Parse a date at the start of s; numeric d/m order depends on the vendor's country."""
    s = s.strip()
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", s)
    if m:
        y, mo, d = map(int, m.groups())
        return fmt(y, mo, d)
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})\b", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
        mo, d = (a, b) if us else (b, a)
        if mo > 12 and d <= 12:
            mo, d = d, mo
        return fmt(y, mo, d)
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\.?\s+(?:of\s+)?([^\W\d_]+)\.?,?\s+(\d{4})", s)
    if m and month(m[2]):
        return fmt(int(m[3]), month(m[2]), int(m[1]))
    m = re.match(r"([^\W\d_]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s)
    if m and month(m[1]):
        return fmt(int(m[3]), month(m[1]), int(m[2]))
    return ""


def month(word):
    w = word.lower()
    return MONTHS.get(w) or MONTHS.get(w[:3])


def fmt(y, mo, d):
    return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else ""


def find_date(text, labels, us, bad_before=()):
    """First date that directly follows one of the labels (tried in priority order)."""
    for label in labels:
        for m in re.finditer(label, text, re.I):
            before = text[max(0, m.start() - 12):m.start()].lower()
            if any(re.search(r"\b" + b + r"\W*$", before) for b in bad_before):
                continue
            line = text[m.start():text.find("\n", m.end()) if "\n" in text[m.end():] else None]
            if re.search(r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,", line, re.I):
                continue  # e-mail header "Date: Mon, ..." is the sending date, not the invoice date
            d = parse_date(re.sub(r"^\s*(?:[:\-]|on|by|of)?\s*", "", text[m.end():m.end() + 40]), us)
            if d:
                return d
    return ""


INV_DATE_LABELS = [r"invoice\s+date", r"date\s+of\s+(?:issue|invoice)", r"issue\s+date",
                   r"\bissued(?:\s+on)?", r"\bdated", r"tax\s+point", r"rechnungsdatum",
                   r"date\s+de\s+facture", r"\bdatum", r"\bdate\b"]
DUE_LABELS = [r"due\s+date", r"payment\s+due(?:\s+date)?", r"\bdue(?:\s+(?:on|by))?",
              r"pay(?:able)?\s+by", r"f[äa]llig(?:keitsdatum)?(?:\s+am)?", r"[ée]ch[ée]ance"]

TOKEN = r"((?=[A-Za-z0-9/._-]*\d)[A-Za-z0-9][A-Za-z0-9/._-]*[A-Za-z0-9])"
NUM_LABEL = r"\s*(?:no\.?|number|num\.?|#|nr\.?|ref\.?|id)?\s*[:#.]?\s*"


def find_number(text, credit):
    labels = [r"credit\s*(?:note|memo)" + NUM_LABEL, r"invoice" + NUM_LABEL,
              r"\b(?:inv|document|doc|bill)\.?\s*(?:no\.?|number|#)\s*[:#.]?\s*",
              r"\bref(?:erence)?\.?" + NUM_LABEL, r"\b(?:no|number|nr)\.?\s*[:#]\s*"]
    if not credit:
        labels.pop(0)
    for label in labels:
        for m in re.finditer(label + TOKEN, text, re.I):
            tok = m.group(1)
            if re.match(r"(?i)po\b|po-|\d{1,4}[./-]\d{1,2}[./-]\d{1,4}$", tok):
                continue
            return tok
    return ""


def classify(text):
    lines = [l for l in text.splitlines() if l.strip()]
    head = "\n".join(lines[:6])
    fwd = re.search(r"forwarded message|^-{3,}.*original", text, re.I | re.M)
    if fwd:  # also look at the first lines of the forwarded content
        head += "\n" + "\n".join(l for l in text[fwd.end():].splitlines() if l.strip())[:300]
    for scope in (head, text):
        hits = []
        for rx, kind in DOC_KEYWORDS:
            m = re.search(rx, scope, re.I)
            if m:
                hits.append((m.start(), kind))
        if hits:
            return min(hits)[1]
    return "skip"


def find_vendor(lines, text):
    m = re.search(r"^subject:.*\bfrom\s+(.+?)\s*$", text, re.I | re.M)
    if m:
        return m[1].strip(), None
    m = re.search(r"(?:regards|sincerely|thanks|thank you|best|cheers),?\s*\n\s*(.+)", text, re.I)
    if m and re.search(r"forwarded|^subject:", text, re.I | re.M):
        return m[1].strip(), m.end()
    for raw in lines:
        line = raw.split("|")[0].strip(" -=*#:")
        if not line or re.match(r"(?i)(subject|from|to|date|sent|re|fwd?)\s*:", line):
            continue
        if re.fullmatch(r"(?i)(tax\s+)?invoice|credit\s*note|statement.*|account statement", line):
            continue
        line = re.sub(r"(?i)^(from|seller|supplier|issued by)\s*:?\s*", "", line)
        return line, None
    return "", None


def is_us(text, vendor, currency):
    idx = text.find(vendor) if vendor else -1
    ctx = text[idx:idx + 200] if idx >= 0 else text[:300]
    if re.search(r"\b(USA|U\.S\.A?\.?|United States)\b|,\s*[A-Z]{2}(?:\s+\d{5})?\s*(?:,|$)", ctx, re.M):
        return True
    if re.search(r"United Kingdom|\bUK\b|England|Scotland|Wales|Ireland|France|Germany|Deutschland"
                 r"|Spain|Italy|Netherlands|Belgium|Austria|Portugal|Europe", ctx):
        return False
    return currency == "USD"


def country_currency(text):
    if re.search(r"United Kingdom|UK|England|Scotland|Wales|London", text):
        return "GBP"
    return "EUR" if re.search(r"France|Germany|Deutschland|Spain|Italy|Netherlands|Belgium|"
                              r"Austria|Portugal|Ireland|Finland", text) else ""


def find_currency(text):
    counts = {c: len(re.findall(rx, text, re.I)) for c, rx in
              (("USD", r"\bUSD\b|US\$|\$|dollars?"), ("EUR", r"\bEUR\b|€|euros?"),
               ("GBP", r"\bGBP\b|£|pounds?\s+sterling|\bsterling"))}
    best = max(counts, key=counts.get)
    return best if counts[best] else ""


def find_amounts(text):
    """Classify each amount by the words just before it: subtotal, tax or total."""
    found = {"subtotal": [], "tax": [], "total": []}
    for line in text.splitlines():
        prev = 0
        for m in AMOUNT.finditer(line):
            ctx = re.sub(r"\d+(?:[.,]\d+)?\s*%", "", line[prev:m.start()]).strip().lower()[-40:]
            ctx = re.split(r"\d", ctx)[-1] if re.search(r"[a-z]", re.split(r"\d", ctx)[-1]) else ctx
            prev = m.end()
            if re.search(r"paid|payment|deposit|\bless\b|received|previous|brought|outstanding|discount", ctx):
                continue
            if re.search(r"sub-?\s?total|\bnet\b|excl|excluding|before\s+tax|\bex\.?\s+vat|taxable|"
                         r"w/o|zwischensumme|netto|\bht\b", ctx):
                kind = "subtotal"
            elif re.search(r"total|amount\s+due|balance\s+due|\bdue\b|incl|gesamt|\bttc\b|grand", ctx):
                kind = "total"
            elif re.search(r"\btax\b|\bvat\b|\bgst\b|\bhst\b|mwst|ust\b|tva|\biva\b", ctx):
                kind = "tax"
            else:
                continue
            prio = 0 if re.search(r"total", ctx) else 1  # "Total" beats "amount/balance due"
            found[kind].append((prio if kind == "total" else 0, to_number(m)))
    out = {}
    for kind, vals in found.items():
        if vals:
            best = min(v[0] for v in vals)
            out[kind] = [v[1] for v in vals if v[0] == best][-1]  # last one = summary block
    return out


def money(v):
    return "" if v is None else f"{v:.2f}"


def statement_rows(text, fname, vendor, currency, us):
    rows = []
    header = ""
    for line in text.splitlines():
        if re.search(r"(?i)\bdate\b.*\b(amount|total|balance)\b", line):
            header = line.lower()
        if re.search(r"(?i)payment|paid|receipt|brought forward|opening|closing|total|balance\s+due", line):
            continue
        num = re.search(r"\b(?!PO\b|PO-)([A-Z]{1,5}-?\d{3,}[A-Z0-9-]*)\b", line)
        amts = list(AMOUNT.finditer(line))
        if not num or not amts:
            continue
        dates = [d for d in (parse_date(line[m.start():], us) for m in
                 re.finditer(r"(?<![\w.,/-])(?=\d|[A-Z][a-z])", line)) if d]
        credit = bool(re.search(r"(?i)credit|\bCN", line)) or to_number(amts[0]) < 0
        total = abs(to_number(amts[0])) * (-1 if credit else 1)
        rows.append({"file": fname, "invoice_number": num[1], "vendor": vendor,
                     "doc_type": "credit_note" if credit else "invoice",
                     "invoice_date": dates[0] if dates else "",
                     "due_date": dates[1] if len(dates) > 1 and "due" in header else "",
                     "currency": currency, "subtotal": "", "tax": "", "total": money(total),
                     "needs_review": "no"})
    return rows


def process(path):
    fname = os.path.basename(path)
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    kind = classify(text)
    if kind == "skip":
        return [], "skipped (not an invoice)"
    lines = [l for l in text.splitlines() if l.strip()]
    vendor, _ = find_vendor(lines, text)
    currency = find_currency(text)
    us = is_us(text, vendor, currency)
    if not currency:  # no symbol or code printed: fall back to the vendor's home currency
        currency = "USD" if us else country_currency(text)
    if kind == "statement":
        rows = statement_rows(text, fname, vendor, currency, us)
        return rows, f"statement with {len(rows)} invoices"
    credit = kind == "credit_note"
    a = find_amounts(text)
    sub, tax, tot = a.get("subtotal"), a.get("tax"), a.get("total")
    if credit:
        sub, tax, tot = (None if v is None else -abs(v) for v in (sub, tax, tot))
    review = "no"
    if None not in (sub, tax, tot) and abs(sub + tax - tot) > 0.005:
        review = "yes"
    row = {"file": fname, "invoice_number": find_number(text, credit), "vendor": vendor,
           "doc_type": "credit_note" if credit else "invoice",
           "invoice_date": find_date(text, INV_DATE_LABELS, us,
                                     ("due", "delivery", "order", "payment", "service", "sent")),
           "due_date": find_date(text, DUE_LABELS, us, ("amount", "balance", "total")),
           "currency": currency, "subtotal": money(sub), "tax": money(tax), "total": money(tot),
           "needs_review": review}
    return [row], f"{row['doc_type']} {row['invoice_number']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", default="/work/inputs")
    ap.add_argument("--out", default="/work/output.csv")
    args = ap.parse_args()
    seen = {}
    for name in sorted(os.listdir(args.inputs)):
        if not name.lower().endswith(".txt"):
            continue
        rows, msg = process(os.path.join(args.inputs, name))
        for row in rows:
            key = (row["invoice_number"].upper(), re.sub(r"[^a-z0-9]", "", row["vendor"].lower()))
            filled = sum(bool(v) for v in row.values()) + (0 if "statement" in msg else 1)
            if key not in seen or filled > seen[key][0]:  # resent: keep the most complete copy
                seen[key] = (filled, row)
        print(f"{name}: {msg}", flush=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for _, row in seen.values():
            w.writerow(row)
    print("done", flush=True)


if __name__ == "__main__":
    main()
