"""Non-AI baseline for invoice-extraction-v2 (rule-based, standard library only).

Written blind: the author saw only the task prompt in task.yaml and the six files in
sample/ (plus sample_key.csv). No generator code, hidden inputs or answer key were read.
Extraction is label/keyword based so it can cope with layouts not in the samples.

Usage: python blind_invoices_v2.py [--inputs /work/inputs] [--out /work/output.csv]
"""
import argparse, csv, re
from datetime import date, timedelta
from pathlib import Path

HEADER = ["file", "invoice_number", "vendor", "invoice_date", "due_date",
          "currency", "subtotal", "tax", "total"]
MONTHS = {m: i + 1 for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split())}
MONTHS.update({"mär": 3, "mai": 5, "okt": 10, "dez": 12, "janv": 1, "févr": 2, "avr": 4,
               "juin": 6, "juil": 7, "août": 8, "déc": 12, "mrt": 3, "mei": 5})
US_HINTS = r"\b(USA|U\.S\.A?\.?|United States|[A-Z][a-z]+, (?:AL|AK|AZ|CA|CO|CT|DC|FL|GA|IL|IN|MA|MD|MI|MN|MO|NC|NJ|NY|OH|OR|PA|TN|TX|UT|VA|WA|WI)\b)"
UK_HINTS = r"\b(United Kingdom|UK|England|Scotland|Wales|London|Leeds|Manchester|Bristol|Edinburgh|Glasgow|Birmingham)\b"
EU_HINTS = r"\b(Netherlands|Germany|Deutschland|France|Spain|Italy|Belgium|Austria|Ireland|Portugal|Finland|Nederland|Amsterdam|Rotterdam|Utrecht|Berlin|Munich|München|Hamburg|Paris|Lyon|Madrid|Barcelona|Milan|Rome|Brussels|Vienna|Dublin|Lisbon)\b"
INVOICE_WORDS = r"invoice|rechnung|facture|factuur|fattura|factura"
DECOY_WORDS = (r"quotation|quote|estimate|offerte|angebot|devis|payment receipt|receipt|"
               r"purchase order|order confirmation|credit note|remittance|statement of account|pro.?forma")
COMPANY_SUFFIX = r"\b(Ltd|Limited|LLC|Inc|Corp|Co\.|Company|GmbH|AG|B\.V\.|N\.V\.|S\.A\.|SAS|SARL|S\.r\.l\.|plc|LLP|Services|Studio|Group|Supply|Partners)\b"
KEEP_UPPER = {"IT", "LLC", "LLP", "PLC", "AG", "SA", "SAS", "UK", "US", "USA", "HR", "AV", "PR", "BV", "NV", "GMBH", "II", "III"}
AMOUNT = r"(?<![\d.,])-?\d{1,3}(?:[,. ' ]\d{3})*(?:[.,]\d{1,2})?(?![\d%])|(?<![\d.,])-?\d+(?:[.,]\d{1,2})?(?![\d%.,])"
FIELD_LABELS = {  # label regexes for amounts, most specific first
    "subtotal": r"sub-?\s?total|total net|net total|net amount|total excl[\w.]*(?: (?:vat|tax))?|amount before tax|net|zwischensumme|nettobetrag|montant ht|total ht",
    "tax": r"vat/tax|sales tax|vat amount|tax amount|total tax|total vat|vat|tax|gst|hst|mwst|ust|btw|tva|iva",
    "total": r"grand total|invoice total|total amount|total incl[\w.]*(?: (?:vat|tax))?|total due|total|amount due|balance due|amount payable|gesamtbetrag|montant ttc|total ttc|totaal",
}
ALL_LABELS = "|".join(FIELD_LABELS.values())


def country(text):
    if re.search(US_HINTS, text): return "US"
    if re.search(UK_HINTS, text): return "UK"
    if re.search(EU_HINTS, text): return "EU"
    return None


def parse_amount(s):
    s = s.replace(" ", "").replace("'", "").replace(" ", "")
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        parts = s.split(sep)
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            s = parts[0] + "." + parts[1]
        else:
            s = s.replace(sep, "")
    try: return round(float(s), 2)
    except ValueError: return None


def parse_date(s, ctry):
    """Return the first date found in s as a date object, or None."""
    s = s.lower()
    mon = r"([a-zäéû]{3,9})\.?"
    try:
        if m := re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.search(mon + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s):
            if m[1][:4] in MONTHS or m[1][:3] in MONTHS:
                return date(int(m[3]), MONTHS.get(m[1][:4], MONTHS.get(m[1][:3])), int(m[2]))
        if m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\.?[\s-]+(?:of\s+)?" + mon + r",?[\s-]+(\d{2,4})\b", s):
            if m[2][:4] in MONTHS or m[2][:3] in MONTHS:
                y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
                return date(y, MONTHS.get(m[2][:4], MONTHS.get(m[2][:3])), int(m[1]))
        if m := re.search(r"\b(\d{1,2})([-/.])(\d{1,2})\2(\d{2,4})\b", s):
            a, b, y = int(m[1]), int(m[3]), int(m[4]) + (2000 if len(m[4]) == 2 else 0)
            mdy = (ctry == "US" or b > 12) and a <= 12
            if a > 12: mdy = False
            return date(y, a, b) if mdy else date(y, b, a)
    except ValueError:
        return None
    return None


def find_date(text, labels, ctry, exclude=r"due|order|delivery|ship|service|po\b|payment"):
    for lab in labels:
        for m in re.finditer(r"\b(?:" + lab + r")\b[\s:#.]*", text, re.I):
            if lab == "date" and re.search(exclude, text[max(0, m.start() - 15):m.start()], re.I):
                continue
            d = parse_date(text[m.end():m.end() + 40].split("\n")[0], ctry)
            if d: return d
    return None


def amounts_for(text, field):
    """All amounts that follow a label for `field`, in document order."""
    found = []
    for m in re.finditer(r"(?<![\w-])(?:" + FIELD_LABELS[field] + r")\b", text, re.I):
        if field == "total" and re.match(r"\s*(net|excl|before|tax|vat)", text[m.end():], re.I):
            continue
        if field == "tax" and re.match(r"[ \t]*(invoice|id|no|number|reg|exempt)", text[m.end():], re.I):
            continue
        if field == "subtotal" and re.match(r"\s*\d{1,3}\s*(days|$)", text[m.end():].split("\n")[0], re.I):
            continue  # payment terms such as "Net 30"
        rest = re.sub(r"\(?\d+(?:[.,]\d+)?\s*%\)?", " ", text[m.end():].split("\n")[0])  # drop tax rates
        rest = re.split(r"\)|\.\s+[A-Z]|;", rest)[0]  # stop at clause end in prose
        stop = re.search(r"(?<![\w-])(?:" + ALL_LABELS + r")\b", rest, re.I)
        seg = rest[:stop.start()] if stop else rest
        vals = [parse_amount(a) for a in re.findall(AMOUNT, seg)]
        vals = [v for v in vals if v is not None]
        if vals: found.append(vals[-1])
    return found


def pick_amounts(text):
    subs, taxes, tots = (amounts_for(text, f) for f in ("subtotal", "tax", "total"))
    for t in reversed(tots):
        for s in subs:
            for x in taxes:
                if abs(s + x - t) < 0.015: return s, x, t
    s, x, t = (subs[0] if subs else None), (taxes[0] if taxes else None), (max(tots) if tots else None)
    if s is not None and t is not None and x is None: x = round(t - s, 2)
    if x is not None and t is not None and s is None: s = round(t - x, 2)
    if s is not None and x is not None and t is None: t = round(s + x, 2)
    return s, x, t


def tidy_name(name):
    name = re.sub(r"\s+", " ", name).strip(" ,.|-:") + ("." if name.strip().endswith(".") else "")
    if name.isupper():
        name = " ".join(w if w.upper().replace(".", "") in KEEP_UPPER or "." in w[:-1]
                        else w.capitalize() for w in name.split())
    return name


def find_vendor(text):
    if m := re.search(r"^\s*(?:seller|supplier|vendor|issued by|bill(?:ed)? from|from)\s*:\s*(.+)$", text, re.I | re.M):
        if "@" not in m[1]: return tidy_name(m[1].split("|")[0])
    if m := re.search(r"(?:" + INVOICE_WORDS + r")\b[^\n]*?\bfrom\s+([^\n<]+)", text, re.I):
        return tidy_name(m[1])
    lines = [l.strip() for l in text.splitlines() if l.strip() and not set(l.strip()) <= set("-=_*")]
    cands = []
    for l in lines[:6]:
        part = l.split("|")[0].strip()
        if re.match(r"(subject|to|cc|date|hi|hello|dear|re|fwd)\b", part, re.I): continue
        if re.fullmatch(r"(tax\s+)?(" + INVOICE_WORDS + "|" + DECOY_WORDS + r")\b.*", part, re.I): continue
        if re.search(r"\d", part) or ":" in part: continue
        cands.append(part)
    if m := re.search(r"(?:regards|sincerely|thanks|thank you|best),?\s*\n+\s*([^\n]+)", text, re.I):
        cands.append(m[1].strip())
    for c in cands:
        if re.search(COMPANY_SUFFIX, c, re.I): return tidy_name(c)
    return tidy_name(cands[0]) if cands else ""


def find_currency(text, ctry):
    counts = {c: len(re.findall(r"\b" + c + r"\b", text)) for c in ("USD", "EUR", "GBP")}
    counts["USD"] += len(re.findall(r"\$|\bdollars?\b", text, re.I))
    counts["EUR"] += len(re.findall(r"€|\beuros?\b", text, re.I))
    counts["GBP"] += len(re.findall(r"£|\bpounds?\b|\bsterling\b", text, re.I))
    best = max(counts, key=counts.get)
    if counts[best]: return best
    return {"US": "USD", "UK": "GBP", "EU": "EUR"}.get(ctry, "USD")


def is_invoice(text):
    m = re.search(r"\b(" + INVOICE_WORDS + "|" + DECOY_WORDS + r")\b", text, re.I)
    return bool(m) and re.fullmatch(INVOICE_WORDS, m[1], re.I) is not None


def extract(text):
    ctry = country(text)
    num = ""
    for lab in [r"invoice\s*(?:no\.?|number|num|#|nr\.?|id)", r"inv\.?\s*(?:no\.?|#)", r"rechnungs?-?nr\.?|facture n[°o]",
                r"invoice", r"ref(?:erence)?\.?(?:\s*no\.?)?", r"document\s*(?:no\.?|#)", r"no\.?|#"]:
        for m in re.finditer(r"\b(?:" + lab + r")[\s:#.]*([A-Z0-9][A-Z0-9/_.-]*\d[A-Z0-9]*)", text, re.I):
            if not parse_date(m[1], ctry) and not re.match(r"PO-", m[1], re.I):
                num = m[1]; break
        if num: break
    inv = find_date(text, [r"invoice date|date of issue|issue date|issued(?: on)?|dated|invoice dated|datum|date"], ctry)
    due = find_date(text, [r"due date|payment due|due by|due on|payable by|pay by|due|fällig(?:keit)?|échéance"], ctry, exclude=r"$^")
    if inv and not due and (m := re.search(r"\bnet\s*(\d{1,3})\b|\bwithin\s+(\d{1,3})\s+days", text, re.I)):
        due = inv + timedelta(days=int(m[1] or m[2]))
    sub, tax, tot = pick_amounts(text)
    fmt = lambda v: "" if v is None else f"{v:.2f}"
    return [num, find_vendor(text), inv.isoformat() if inv else "", due.isoformat() if due else "",
            find_currency(text, ctry), fmt(sub), fmt(tax), fmt(tot)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", default="/work/inputs")
    ap.add_argument("--out", default="/work/output.csv")
    args = ap.parse_args()
    rows = []
    for path in sorted(Path(args.inputs).glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not is_invoice(text):
            print(f"{path.name}: skipped (not an invoice)", flush=True); continue
        rows.append([path.name] + extract(text))
        print(f"{path.name}: {rows[-1][1]} {rows[-1][2]} total={rows[-1][8]}", flush=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(HEADER); w.writerows(rows)
    print("done", flush=True)


if __name__ == "__main__":
    main()
