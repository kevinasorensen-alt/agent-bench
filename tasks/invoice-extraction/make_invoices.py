"""Generate the invoice-extraction task: fictional invoices in mixed layouts plus the answer key.

Seeded, so the published inputs and key can always be rebuilt exactly. All vendors are made up.
Run: python agent-bench/tasks/invoice-extraction/make_invoices.py
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20261028
COUNT = 20
FIELDS = ["invoice_number", "vendor", "invoice_date", "due_date", "currency", "subtotal", "tax", "total"]

VENDORS = ["Brightwater Supply Co.", "Northgate Office Partners", "Kettle & Pine Catering", "Lumen Print Studio",
           "Harbor Line Logistics", "Copperleaf Cleaning Ltd", "Redfern IT Services", "Mosaic Event Rentals",
           "Tallgrass Landscaping", "Orbit Courier Group", "Silverpeak Consulting", "Juniper Web Design"]
ITEMS = ["Consulting hours", "Monthly retainer", "Printing (A4, colour)", "Delivery fee", "Cleaning service",
         "Software licence", "Catering for 25", "Equipment rental", "Website maintenance", "Courier run"]
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}


def money(x):
    return f"{x:,.2f}"


def fmt_date(d, style):
    if style == "iso":
        return d.isoformat()
    if style == "long":
        return f"{d:%B} {d.day}, {d.year}"
    return f"{d.day} {d:%b} {d.year}"


def make(rng, i):
    cur = rng.choice(["USD", "USD", "EUR", "GBP"])
    issued = date(2026, 1, 5) + timedelta(days=rng.randint(0, 270))
    due = issued + timedelta(days=rng.choice([14, 30, 45]))
    lines = [(rng.choice(ITEMS), rng.randint(1, 12), round(rng.uniform(15, 900), 2)) for _ in range(rng.randint(1, 4))]
    subtotal = round(sum(q * p for _, q, p in lines), 2)
    rate = rng.choice([0.0, 0.05, 0.08, 0.2])
    tax = round(subtotal * rate, 2)
    rec = {"invoice_number": f"{rng.choice(['INV', 'IN', 'BW', 'NO'])}-{rng.randint(1000, 99999)}",
           "vendor": rng.choice(VENDORS), "invoice_date": issued.isoformat(), "due_date": due.isoformat(),
           "currency": cur, "subtotal": f"{subtotal:.2f}", "tax": f"{tax:.2f}", "total": f"{subtotal + tax:.2f}"}
    return rec, lines, rate, issued, due


def render(rng, rec, lines, rate, issued, due):
    cur, sym = rec["currency"], SYMBOL[rec["currency"]]
    sub, tax, tot = (float(rec[k]) for k in ("subtotal", "tax", "total"))
    layout = rng.choice(["plain", "table", "email"])
    if layout == "plain":
        body = [rec["vendor"].upper(), "INVOICE", "", f"Invoice No: {rec['invoice_number']}",
                f"Date: {fmt_date(issued, rng.choice(['iso', 'long', 'short']))}",
                f"Due: {fmt_date(due, rng.choice(['iso', 'long', 'short']))}", ""]
        body += [f"  {name} x{q} @ {sym}{money(p)}" for name, q, p in lines]
        body += ["", f"Subtotal: {sym}{money(sub)}", f"Tax ({rate:.0%}): {sym}{money(tax)}", f"TOTAL: {sym}{money(tot)}"]
    elif layout == "table":
        body = [f"{rec['vendor']}  |  Tax invoice", "-" * 52,
                f"Ref {rec['invoice_number']}   Issued {fmt_date(issued, 'short')}   Payment due {fmt_date(due, 'short')}",
                "-" * 52, f"{'Item':<26}{'Qty':>5}{'Amount (' + cur + ')':>21}"]
        body += [f"{name:<26}{q:>5}{money(q * p):>21}" for name, q, p in lines]
        body += ["-" * 52, f"{'Net':<31}{money(sub):>21}", f"{'VAT/Tax':<31}{money(tax):>21}",
                 f"{'Amount due':<31}{money(tot):>21}"]
    else:
        body = [f"Subject: Fwd: Invoice {rec['invoice_number']} from {rec['vendor']}", "",
                "Hi team, forwarding this for processing.", "", "---------- Forwarded message ----------",
                f"Hello, please find our invoice {rec['invoice_number']} dated {fmt_date(issued, 'long')} "
                f"for a total of {cur} {money(tot)} (net {money(sub)} plus tax {money(tax)}). "
                f"Payment is due by {fmt_date(due, 'long')}.", "", f"Kind regards,", rec["vendor"]]
    return "\n".join(body) + "\n"


def main():
    rng = random.Random(SEED)
    inputs = HERE / "inputs"
    inputs.mkdir(exist_ok=True)
    rows = []
    for i in range(1, COUNT + 1):
        rec, lines, rate, issued, due = make(rng, i)
        name = f"invoice_{i:02d}.txt"
        (inputs / name).write_text(render(rng, rec, lines, rate, issued, due), encoding="utf-8")
        rows.append({"file": name, **rec})
    with open(HERE / "answer_key.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["file"] + FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {COUNT} invoices and answer_key.csv")


if __name__ == "__main__":
    main()
