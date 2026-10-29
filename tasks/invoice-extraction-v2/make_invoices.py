"""Generate invoice-extraction-v2: messier invoices, held-out layouts, and non-invoice decoys.

- sample/ (6 invoices + sample_key.csv) uses only the "seen" layouts. It's public: baseline authors may
  read it, and nothing else.
- inputs/ (40 invoices + 3 decoys) mixes seen layouts with held-out ones that never appear in sample/.
- answer_key.csv covers inputs/; decoy rows have is_invoice=no and must be left out of the output.

Seeded and written with LF line endings, so the published files rebuild byte for byte on any OS.
Anyone writing a baseline must not read this file. All companies are made up.
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIELDS = ["invoice_number", "vendor", "invoice_date", "due_date", "currency", "subtotal", "tax", "total"]
SEEN = ["plain", "table", "email"]
HELD_OUT = ["letter_eu", "ocr_noisy", "statement", "us_numeric", "uk_numeric", "two_page"]

VENDORS = {
    "USD": [("Brightwater Supply Co.", "Portland, OR, USA"), ("Orbit Courier Group", "Austin, TX, USA"),
            ("Juniper Web Design", "Denver, CO, USA"), ("Redfern IT Services", "Columbus, OH, USA")],
    "EUR": [("Kettle & Pine Catering GmbH", "Hamburg, Germany"), ("Mosaic Event Rentals B.V.", "Utrecht, Netherlands"),
            ("Lumen Print Studio SARL", "Lyon, France")],
    "GBP": [("Copperleaf Cleaning Ltd", "Leeds, United Kingdom"), ("Northgate Office Partners Ltd", "Bristol, United Kingdom"),
            ("Harbor Line Logistics Ltd", "Glasgow, United Kingdom")],
}
ITEMS = ["Consulting hours", "Monthly retainer", "Printing (A4, colour)", "Delivery fee", "Cleaning service",
         "Software licence", "Catering for 25", "Equipment rental", "Website maintenance", "Courier run"]
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}
TAX_RATES = {"USD": [0.0, 0.0625, 0.08], "EUR": [0.19, 0.21, 0.2], "GBP": [0.2, 0.0]}


def money(x):
    return f"{x:,.2f}"


def money_eu(x):
    return f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def long_date(d):
    return f"{d:%B} {d.day}, {d.year}"


def short_date(d):
    return f"{d.day} {d:%b} {d.year}"


LAYOUT_CURRENCY = {"letter_eu": "EUR", "us_numeric": "USD", "uk_numeric": "GBP"}


def make(rng, cur=None, prefix_pool=("INV", "IN", "BW", "NO", "A", "SI")):
    cur = cur or rng.choice(["USD", "USD", "EUR", "GBP"])
    vendor, place = rng.choice(VENDORS[cur])
    issued = date(2026, 1, 13) + timedelta(days=rng.randint(0, 260))
    due = issued + timedelta(days=rng.choice([14, 30, 45, 60]))
    lines = [(rng.choice(ITEMS), rng.randint(1, 12), round(rng.uniform(15, 900), 2)) for _ in range(rng.randint(1, 6))]
    subtotal = round(sum(round(q * p, 2) for _, q, p in lines), 2)
    rate = rng.choice(TAX_RATES[cur])
    tax = round(subtotal * rate, 2)
    number = f"{rng.choice(prefix_pool)}-{rng.randint(1000, 99999)}"
    rec = {"invoice_number": number, "vendor": vendor, "invoice_date": issued.isoformat(), "due_date": due.isoformat(),
           "currency": cur, "subtotal": f"{subtotal:.2f}", "tax": f"{tax:.2f}", "total": f"{subtotal + tax:.2f}"}
    ctx = {"place": place, "lines": lines, "rate": rate, "issued": issued, "due": due, "sub": subtotal, "tax": tax,
           "tot": round(subtotal + tax, 2), "po": f"PO-{rng.randint(10000, 99999)}", "cust": f"C-{rng.randint(100, 9999)}"}
    return rec, ctx


def item_lines(ctx, sym, fmt=money):
    return [f"  {name} x{q} @ {sym}{fmt(p)}" for name, q, p in ctx["lines"]]


def render(rng, layout, rec, c):
    cur, sym, v = rec["currency"], SYMBOL[rec["currency"]], rec["vendor"]
    n, i, d = rec["invoice_number"], c["issued"], c["due"]
    if layout == "plain":
        body = [v.upper(), c["place"], "INVOICE", "", f"Invoice No: {n}", f"Customer No: {c['cust']}",
                f"Date: {rng.choice([i.isoformat(), long_date(i), short_date(i)])}",
                f"Due: {rng.choice([d.isoformat(), long_date(d), short_date(d)])}", ""]
        body += item_lines(c, sym) + ["", f"Subtotal: {sym}{money(c['sub'])}", f"Tax ({c['rate']:.2%}): {sym}{money(c['tax'])}",
                                      f"TOTAL: {sym}{money(c['tot'])}"]
    elif layout == "table":
        body = [f"{v}  |  Tax invoice  |  {c['place']}", "-" * 56,
                f"Ref {n}   Issued {short_date(i)}   Payment due {short_date(d)}   Your PO {c['po']}", "-" * 56,
                f"{'Item':<26}{'Qty':>5}{'Amount (' + cur + ')':>25}"]
        body += [f"{name:<26}{q:>5}{money(q * p):>25}" for name, q, p in c["lines"]]
        body += ["-" * 56, f"{'Net':<31}{money(c['sub']):>25}", f"{'VAT/Tax':<31}{money(c['tax']):>25}",
                 f"{'Amount due':<31}{money(c['tot']):>25}"]
    elif layout == "email":
        body = [f"Subject: Fwd: Invoice {n} from {v}", "", "Hi team, forwarding this for processing. Ref our PO "
                f"{c['po']}.", "", "---------- Forwarded message ----------",
                f"Hello, please find our invoice {n} dated {long_date(i)} for a total of {cur} {money(c['tot'])} "
                f"(net {money(c['sub'])} plus tax {money(c['tax'])}). Payment is due by {long_date(d)}.", "",
                "Kind regards,", v, c["place"]]
    elif layout == "letter_eu":
        body = [v, c["place"], "", f"Rechnung / Invoice {n}", f"Rechnungsdatum: {i:%d.%m.%Y}",
                f"Zahlbar bis / Due: {d:%d.%m.%Y}", f"Kundennummer: {c['cust']}", ""]
        body += [f"  {name}: {q} x {money_eu(p)} €" for name, q, p in c["lines"]]
        body += ["", f"Nettobetrag (net): {money_eu(c['sub'])} €", f"MwSt./VAT {c['rate']:.0%}: {money_eu(c['tax'])} €",
                 f"Gesamtbetrag (total): {money_eu(c['tot'])} €"]
    elif layout == "ocr_noisy":
        def noisy(s):
            return "".join(ch if rng.random() > 0.04 or not ch.isalpha() else {"o": "0", "l": "1", "e": "c", "a": "o"}.get(ch, ch) for ch in s)
        body = [v, noisy(c["place"]), "", f"lNVOICE   {n}", f"Date  {short_date(i)}      Due  {short_date(d)}", "",
                *[noisy(x) for x in item_lines(c, sym)], "", f"Sub total      {cur} {money(c['sub'])}",
                f"Tax            {cur} {money(c['tax'])}", f"Total          {cur} {money(c['tot'])}", noisy("Thank you for your business")]
    elif layout == "statement":
        paid = round(c["tot"] * rng.choice([0.25, 0.5]), 2)
        body = [f"{v} - {c['place']}", f"INVOICE {n}   (Customer {c['cust']})", f"Invoice date: {long_date(i)}",
                f"Terms: due {long_date(d)}", ""]
        body += item_lines(c, sym) + ["", f"Subtotal {sym}{money(c['sub'])}", f"Tax {sym}{money(c['tax'])}",
                                      f"Invoice total {sym}{money(c['tot'])}", f"Less payment received {sym}{money(paid)}",
                                      f"BALANCE DUE {sym}{money(c['tot'] - paid)}", "",
                                      "Remit to: Capital Factoring Services LLC, PO Box 4410"]
    elif layout == "us_numeric":
        body = [v, c["place"], "", f"Invoice #: {n}", f"Invoice Date: {i:%m/%d/%Y}", f"Due Date: {d:%m/%d/%Y}",
                f"PO #: {c['po']}", ""] + item_lines(c, sym) + [
            "", f"Subtotal: {money(c['sub'])} {cur}", f"Sales tax: {money(c['tax'])} {cur}", f"Total due: {money(c['tot'])} {cur}"]
    elif layout == "uk_numeric":
        body = [v, c["place"], "", f"Invoice number {n}", f"Tax point / date: {i:%d/%m/%Y}", f"Payment due: {d:%d/%m/%Y}", ""]
        body += item_lines(c, sym) + ["", f"Net amount  {sym}{money(c['sub'])}", f"VAT         {sym}{money(c['tax'])}",
                                      f"Total       {sym}{money(c['tot'])}"]
    else:  # two_page
        body = [f"{v}                                   Page 1 of 2", c["place"], "", f"INVOICE {n}",
                f"Issued {long_date(i)}", ""] + item_lines(c, sym) + ["", "(continued on page 2)", "", "\f",
                f"{v}  -  Invoice {n}                Page 2 of 2", "", f"Carried forward: {sym}{money(c['sub'])}",
                f"Tax: {sym}{money(c['tax'])}", f"Total payable: {sym}{money(c['tot'])}", f"Please pay by {short_date(d)}."]
    return "\n".join(body) + "\n"


def decoy(rng, kind):
    rec, c = make(rng)
    sym = SYMBOL[rec["currency"]]
    head = {"quote": ["QUOTATION - this is not an invoice", f"Quote ref Q-{rng.randint(1000, 9999)}, valid 30 days"],
            "receipt": ["PAYMENT RECEIPT", f"Thank you. We received your payment for invoice {rec['invoice_number']}."],
            "po": [f"PURCHASE ORDER {c['po']}", "Please supply the following and invoice us on delivery."]}[kind]
    body = [rec["vendor"], c["place"], ""] + head + [f"Date: {long_date(c['issued'])}", ""] + item_lines(c, sym) + [
        "", f"Total: {sym}{money(c['tot'])}"]
    return "\n".join(body) + "\n"


def write(folder, files):
    folder.mkdir(exist_ok=True)
    for name, text in files:
        with open(folder / name, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def write_key(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["file", "is_invoice", "layout"] + FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    rng = random.Random(20261028)
    files, rows = [], []
    layouts = SEEN * 7 + HELD_OUT * 3 + ["two_page"]  # 21 seen + 19 held-out = 40
    rng.shuffle(layouts)
    names = [f"doc_{k:02d}.txt" for k in range(1, 44)]
    rng.shuffle(names)  # decoys aren't always the last files
    for name, layout in zip(names, layouts):
        rec, c = make(rng, LAYOUT_CURRENCY.get(layout))
        files.append((name, render(rng, layout, rec, c)))
        rows.append({"file": name, "is_invoice": "yes", "layout": layout, **rec})
    for name, kind in zip(names[40:], ["quote", "receipt", "po"]):
        files.append((name, decoy(rng, kind)))
        rows.append({"file": name, "is_invoice": "no", "layout": f"decoy-{kind}", **{f: "" for f in FIELDS}})
    write(HERE / "inputs", sorted(files))
    write_key(HERE / "answer_key.csv", sorted(rows, key=lambda r: r["file"]))

    srng = random.Random(77)
    sample_files, sample_rows = [], []
    for k, layout in enumerate(SEEN * 2, start=1):
        rec, c = make(srng)
        sample_files.append((f"sample_{k:02d}.txt", render(srng, layout, rec, c)))
        sample_rows.append({"file": f"sample_{k:02d}.txt", "is_invoice": "yes", "layout": layout, **rec})
    write(HERE / "sample", sample_files)
    write_key(HERE / "sample" / "sample_key.csv", sample_rows)
    print(f"inputs: {len(files)} files (40 invoices, 3 decoys); sample: {len(sample_files)} invoices")


if __name__ == "__main__":
    main()
