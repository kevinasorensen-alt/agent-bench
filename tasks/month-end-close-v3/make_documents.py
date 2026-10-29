"""Generate month-end-close-v3: a month of supplier documents with the traps a bookkeeper actually meets.

- 110 single invoices in 9 layouts (10 with no due date anywhere, 6 whose printed total doesn't add up)
- 8 credit notes (no due date; positive amounts printed, negative in the answer)
- 2 account statements, each listing 3 invoices (one row per listed invoice)
- 8 resends: invoices sent a second time in another form (list each invoice once)
- 10 decoys: quotes, purchase orders, payment receipts, remittance advice (leave out)

sample/ is public and shows only the "seen" layouts plus one credit note and one invoice without a due date.
answer_key.csv has one row per invoice or credit note; `files` lists every file that contains it.
Seeded, LF line endings, byte-identical rebuild. Anyone writing a baseline must not read this file.
All companies are made up.
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIELDS = ["vendor", "doc_type", "invoice_date", "due_date", "currency", "subtotal", "tax", "total", "needs_review"]
SEEN = ["plain", "table", "email"]
HELD_OUT = ["letter_eu", "ocr_noisy", "statement", "us_numeric", "uk_numeric", "two_page"]
LAYOUT_CURRENCY = {"letter_eu": "EUR", "us_numeric": "USD", "uk_numeric": "GBP"}
NO_DUE_OK = ["plain", "table", "email", "letter_eu", "us_numeric", "uk_numeric"]  # layouts that can drop the due line

VENDORS = {
    "USD": [("Brightwater Supply Co.", "Portland, OR, USA"), ("Orbit Courier Group", "Austin, TX, USA"),
            ("Juniper Web Design", "Denver, CO, USA"), ("Redfern IT Services", "Columbus, OH, USA"),
            ("Cascade Office Furniture Inc.", "Tacoma, WA, USA")],
    "EUR": [("Kettle & Pine Catering GmbH", "Hamburg, Germany"), ("Mosaic Event Rentals B.V.", "Utrecht, Netherlands"),
            ("Lumen Print Studio SARL", "Lyon, France"), ("Nordlicht Software GmbH", "Munich, Germany")],
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


class Numbers:
    """Unique document numbers across the whole set (resends reuse their original's number on purpose)."""
    def __init__(self, rng):
        self.rng, self.used = rng, set()

    def new(self, prefixes=("INV", "IN", "BW", "NO", "A", "SI")):
        while True:
            n = f"{self.rng.choice(prefixes)}-{self.rng.randint(1000, 99999)}"
            if n not in self.used:
                self.used.add(n)
                return n


def make(rng, nums, cur=None, prefixes=None):
    cur = cur or rng.choice(["USD", "USD", "EUR", "GBP"])
    vendor, place = rng.choice(VENDORS[cur])
    issued = date(2026, 9, 1) + timedelta(days=rng.randint(0, 29))  # one month of documents
    due = issued + timedelta(days=rng.choice([14, 30, 45, 60]))
    lines = [(rng.choice(ITEMS), rng.randint(1, 12), round(rng.uniform(15, 900), 2)) for _ in range(rng.randint(1, 6))]
    sub = round(sum(round(q * p, 2) for _, q, p in lines), 2)
    rate = rng.choice(TAX_RATES[cur])
    tax = round(sub * rate, 2)
    number = nums.new(prefixes) if prefixes else nums.new()
    return {"invoice_number": number, "vendor": vendor, "place": place, "cur": cur, "issued": issued, "due": due,
            "lines": lines, "sub": sub, "rate": rate, "tax": tax, "tot": round(sub + tax, 2),
            "po": f"PO-{rng.randint(10000, 99999)}", "cust": f"C-{rng.randint(100, 9999)}", "has_due": True}


def key_row(d, doc_type="invoice", review="no"):
    sign = -1 if doc_type == "credit_note" else 1
    return {"invoice_number": d["invoice_number"], "vendor": d["vendor"], "doc_type": doc_type,
            "invoice_date": d["issued"].isoformat(), "due_date": d["due"].isoformat() if d["has_due"] else "",
            "currency": d["cur"], "subtotal": amount(sign * d["sub"]), "tax": amount(sign * d["tax"]),
            "total": amount(sign * d["tot"]), "needs_review": review}


def amount(x):
    return f"{0.0 if x == 0 else x:.2f}"  # never "-0.00" (a zero-tax credit note)


def item_lines(d, fmt=money):
    sym = SYMBOL[d["cur"]]
    return [f"  {name} x{q} @ {sym}{fmt(p)}" for name, q, p in d["lines"]]


def render(rng, layout, d):
    cur, sym, v, n, i, du = d["cur"], SYMBOL[d["cur"]], d["vendor"], d["invoice_number"], d["issued"], d["due"]
    due_line = lambda text: [text] if d["has_due"] else []
    if layout == "plain":
        body = [v.upper(), d["place"], "INVOICE", "", f"Invoice No: {n}", f"Customer No: {d['cust']}",
                f"Date: {rng.choice([i.isoformat(), long_date(i), short_date(i)])}",
                *due_line(f"Due: {rng.choice([du.isoformat(), long_date(du), short_date(du)])}"), ""]
        body += item_lines(d) + ["", f"Subtotal: {sym}{money(d['sub'])}", f"Tax ({d['rate']:.2%}): {sym}{money(d['tax'])}",
                                 f"TOTAL: {sym}{money(d['tot'])}"]
    elif layout == "table":
        ref = f"Ref {n}   Issued {short_date(i)}" + (f"   Payment due {short_date(du)}" if d["has_due"] else "")
        body = [f"{v}  |  Tax invoice  |  {d['place']}", "-" * 60, f"{ref}   Your PO {d['po']}", "-" * 60,
                f"{'Item':<26}{'Qty':>5}{'Amount (' + cur + ')':>29}"]
        body += [f"{name:<26}{q:>5}{money(q * p):>29}" for name, q, p in d["lines"]]
        body += ["-" * 60, f"{'Net':<31}{money(d['sub']):>29}", f"{'VAT/Tax':<31}{money(d['tax']):>29}",
                 f"{'Amount due':<31}{money(d['tot']):>29}"]
    elif layout == "email":
        due_txt = f" Payment is due by {long_date(du)}." if d["has_due"] else ""
        body = [f"Subject: Fwd: Invoice {n} from {v}", "", f"Hi team, forwarding this for processing. Ref our PO {d['po']}.",
                "", "---------- Forwarded message ----------",
                f"Hello, please find our invoice {n} dated {long_date(i)} for a total of {cur} {money(d['tot'])} "
                f"(net {money(d['sub'])} plus tax {money(d['tax'])}).{due_txt}", "", "Kind regards,", v, d["place"]]
    elif layout == "letter_eu":
        body = [v, d["place"], "", f"Rechnung / Invoice {n}", f"Rechnungsdatum: {i:%d.%m.%Y}",
                *due_line(f"Zahlbar bis / Due: {du:%d.%m.%Y}"), f"Kundennummer: {d['cust']}", ""]
        body += [f"  {name}: {q} x {money_eu(p)} €" for name, q, p in d["lines"]]
        body += ["", f"Nettobetrag (net): {money_eu(d['sub'])} €", f"MwSt./VAT {d['rate']:.0%}: {money_eu(d['tax'])} €",
                 f"Gesamtbetrag (total): {money_eu(d['tot'])} €"]
    elif layout == "ocr_noisy":
        def noisy(s):
            return "".join(ch if rng.random() > 0.04 or not ch.isalpha() else
                           {"o": "0", "l": "1", "e": "c", "a": "o"}.get(ch, ch) for ch in s)
        body = [v, noisy(d["place"]), "", f"lNVOICE   {n}", f"Date  {short_date(i)}      Due  {short_date(du)}", "",
                *[noisy(x) for x in item_lines(d)], "", f"Sub total      {cur} {money(d['sub'])}",
                f"Tax            {cur} {money(d['tax'])}", f"Total          {cur} {money(d['tot'])}",
                noisy("Thank you for your business")]
    elif layout == "statement":
        paid = round(d["tot"] * rng.choice([0.25, 0.5]), 2)
        body = [f"{v} - {d['place']}", f"INVOICE {n}   (Customer {d['cust']})", f"Invoice date: {long_date(i)}",
                f"Terms: due {long_date(du)}", ""]
        body += item_lines(d) + ["", f"Subtotal {sym}{money(d['sub'])}", f"Tax {sym}{money(d['tax'])}",
                                 f"Invoice total {sym}{money(d['tot'])}", f"Less payment received {sym}{money(paid)}",
                                 f"BALANCE DUE {sym}{money(d['tot'] - paid)}", "",
                                 "Remit to: Capital Factoring Services LLC, PO Box 4410"]
    elif layout == "us_numeric":
        body = [v, d["place"], "", f"Invoice #: {n}", f"Invoice Date: {i:%m/%d/%Y}", *due_line(f"Due Date: {du:%m/%d/%Y}"),
                f"PO #: {d['po']}", ""] + item_lines(d) + [
            "", f"Subtotal: {money(d['sub'])} {cur}", f"Sales tax: {money(d['tax'])} {cur}", f"Total due: {money(d['tot'])} {cur}"]
    elif layout == "uk_numeric":
        body = [v, d["place"], "", f"Invoice number {n}", f"Tax point / date: {i:%d/%m/%Y}",
                *due_line(f"Payment due: {du:%d/%m/%Y}"), ""]
        body += item_lines(d) + ["", f"Net amount  {sym}{money(d['sub'])}", f"VAT         {sym}{money(d['tax'])}",
                                 f"Total       {sym}{money(d['tot'])}"]
    else:  # two_page
        body = [f"{v}                                   Page 1 of 2", d["place"], "", f"INVOICE {n}",
                f"Issued {long_date(i)}", ""] + item_lines(d) + ["", "(continued on page 2)", "", "\f",
                f"{v}  -  Invoice {n}                Page 2 of 2", "", f"Carried forward: {sym}{money(d['sub'])}",
                f"Tax: {sym}{money(d['tax'])}", f"Total payable: {sym}{money(d['tot'])}", f"Please pay by {short_date(du)}."]
    return "\n".join(body) + "\n"


def render_credit(rng, d, ref):
    sym = SYMBOL[d["cur"]]
    body = [d["vendor"], d["place"], "", "CREDIT NOTE", f"Credit note no. {d['invoice_number']}",
            f"Date: {long_date(d['issued'])}", f"Relates to invoice {ref}", "",
            "We have credited your account for the following:"] + item_lines(d) + [
        "", f"Net credit: {sym}{money(d['sub'])}", f"Tax: {sym}{money(d['tax'])}", f"Total credit: {sym}{money(d['tot'])}",
        "", "No payment is required."]
    return "\n".join(body) + "\n"


def render_account_statement(rng, vendor, place, cur, invs, cust):
    rows = [f"{x['invoice_number']:<12}{x['issued'].isoformat():<13}{x['due'].isoformat():<13}"
            f"{money(x['sub']):>12}{money(x['tax']):>11}{money(x['tot']):>12}" for x in invs]
    outstanding = sum(x["tot"] for x in invs)
    body = [f"{vendor} - {place}", f"STATEMENT OF ACCOUNT   Customer {cust}   Amounts in {cur}", "",
            "The following invoices are open on your account:", "",
            f"{'Invoice':<12}{'Date':<13}{'Due':<13}{'Net':>12}{'Tax':>11}{'Total':>12}", *rows, "",
            f"Total outstanding: {money(outstanding)} {cur}", "", "Please pay any overdue amounts promptly."]
    return "\n".join(body) + "\n"


def render_resend(rng, d, kind):
    if kind == "email":
        return "\n".join([f"Subject: RESENT - Invoice {d['invoice_number']} (second copy)", "",
                          f"Hello, we haven't had a reply, so here is invoice {d['invoice_number']} again.",
                          f"Invoice date {long_date(d['issued'])}, total {d['cur']} {money(d['tot'])} "
                          f"(net {money(d['sub'])} plus tax {money(d['tax'])})"
                          + (f", due {long_date(d['due'])}." if d["has_due"] else "."),
                          "", "Thanks,", d["vendor"]]) + "\n"
    return "*** DUPLICATE COPY - ORIGINAL ALREADY SENT ***\n" + render(rng, "plain", d)


def decoy(rng, nums, kind, invoices):
    d = make(rng, nums)
    sym = SYMBOL[d["cur"]]
    if kind == "remittance":
        paid = rng.choice(invoices)
        return "\n".join([f"REMITTANCE ADVICE from {paid['vendor']}'s customer", "",
                          f"We have paid invoice {paid['invoice_number']} in full today: {paid['cur']} {money(paid['tot'])}.",
                          "This is a payment notification, not an invoice."]) + "\n"
    head = {"quote": ["QUOTATION - this is not an invoice", f"Quote ref Q-{rng.randint(1000, 9999)}, valid 30 days"],
            "receipt": ["PAYMENT RECEIPT", "Thank you. We received your payment."],
            "po": [f"PURCHASE ORDER {d['po']}", "Please supply the following and invoice us on delivery."]}[kind]
    body = [d["vendor"], d["place"], ""] + head + [f"Date: {long_date(d['issued'])}", ""] + item_lines(d) + [
        "", f"Total: {sym}{money(d['tot'])}"]
    return "\n".join(body) + "\n"


def write_files(folder, files):
    folder.mkdir(exist_ok=True)
    for old in folder.glob("*.txt"):
        old.unlink()
    for name, text in files:
        with open(folder / name, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def write_key(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["invoice_number", "files", "layout", "trap"] + FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["invoice_number"]))


def main():
    rng = random.Random(20261103)
    nums = Numbers(rng)
    docs = []  # (text, layout, key rows [(row, trap)], decoy flag)
    layouts = SEEN * 15 + HELD_OUT * 10 + ["two_page"] * 5  # 45 seen + 65 held-out = 110
    rng.shuffle(layouts)
    no_due = set(rng.sample([k for k, l in enumerate(layouts) if l in NO_DUE_OK], 10))
    mismatch = set(rng.sample([k for k in range(len(layouts)) if k not in no_due], 6))
    singles = []
    for k, layout in enumerate(layouts):
        d = make(rng, nums, LAYOUT_CURRENCY.get(layout))
        trap = ""
        if k in no_due:
            d["has_due"], trap = False, "no_due_date"
        review = "no"
        if k in mismatch:  # the supplier's own arithmetic is off: copy as printed, flag for review
            d["tot"] = round(d["tot"] + rng.choice([100.0, -10.0, 9.0, 1000.0]), 2)
            review, trap = "yes", "total_mismatch"
        singles.append(d)
        docs.append((render(rng, layout, d), layout, [(key_row(d, review=review), trap)], False))
    for _ in range(8):
        ref = rng.choice(singles)
        c = make(rng, nums, ref["cur"], prefixes=("CN", "CR"))
        c["vendor"], c["place"], c["has_due"] = ref["vendor"], ref["place"], False
        docs.append((render_credit(rng, c, ref["invoice_number"]), "credit_note", [(key_row(c, "credit_note"), "credit_note")], False))
    for _ in range(2):
        cur = rng.choice(["USD", "EUR", "GBP"])
        vendor, place = rng.choice(VENDORS[cur])
        invs = []
        for _ in range(3):
            x = make(rng, nums, cur)
            x["vendor"], x["place"] = vendor, place
            invs.append(x)
        docs.append((render_account_statement(rng, vendor, place, cur, invs, f"C-{rng.randint(100, 9999)}"),
                     "account_statement", [(key_row(x), "account_statement") for x in invs], False))
    resent = rng.sample(singles, 8)
    for k, d in enumerate(resent):
        docs.append((render_resend(rng, d, "email" if k % 2 == 0 else "copy"), "resend", [("resend", d["invoice_number"])], False))
    for kind in ["quote"] * 3 + ["po"] * 3 + ["receipt"] * 2 + ["remittance"] * 2:
        docs.append((decoy(rng, nums, kind, singles), f"decoy-{kind}", [], True))

    rng.shuffle(docs)
    files, rows, extra_files = [], {}, {}
    for idx, (text, layout, keyed, is_decoy) in enumerate(docs, start=1):
        name = f"doc_{idx:03d}.txt"
        files.append((name, text))
        if is_decoy:
            rows[f"DECOY:{name}"] = {"invoice_number": "", "files": name, "layout": layout, "trap": "decoy",
                                     **{f: "" for f in FIELDS}}
            continue
        for row, trap in keyed:
            if row == "resend":
                extra_files.setdefault(trap, []).append(name)
                continue
            rows[row["invoice_number"]] = {**row, "files": name, "layout": layout, "trap": trap}
    for number, names in extra_files.items():
        rows[number]["files"] += ";" + ";".join(names)
        rows[number]["trap"] = (rows[number]["trap"] + "+resent").lstrip("+")
    write_files(HERE / "inputs", files)
    write_key(HERE / "answer_key.csv", rows.values())

    srng = random.Random(77)
    snums = Numbers(srng)
    sample_docs, sample_rows = [], []
    for k, layout in enumerate(SEEN * 2 + ["plain"], start=1):
        d = make(srng, snums)
        if k == 7:
            d["has_due"] = False
        sample_docs.append((f"sample_{k:02d}.txt", render(srng, layout, d)))
        sample_rows.append({**key_row(d), "files": f"sample_{k:02d}.txt", "layout": layout, "trap": "no_due_date" if k == 7 else ""})
    ref = sample_rows[0]["invoice_number"]
    c = make(srng, snums, prefixes=("CN",))
    c["has_due"] = False
    sample_docs.append(("sample_08.txt", render_credit(srng, c, ref)))
    sample_rows.append({**key_row(c, "credit_note"), "files": "sample_08.txt", "layout": "credit_note", "trap": "credit_note"})
    write_files(HERE / "sample", sample_docs)
    write_key(HERE / "sample" / "sample_key.csv", sample_rows)
    n_inv = sum(1 for r in rows.values() if r["trap"] != "decoy")
    print(f"inputs: {len(files)} files; answer key: {n_inv} invoices and credit notes ({n_inv * len(FIELDS)} points); "
          f"sample: {len(sample_docs)} documents")


if __name__ == "__main__":
    main()
