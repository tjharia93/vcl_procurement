"""Purchasing business rules, in plain Python.

Nothing in this file imports frappe. That is deliberate: every rule that used to
live in the browser (the /purchasing-dev prototype) is written here once, so it
can be unit tested without a bench and so the Compass screen, the Android app and
the Desk all get the same answer.

    python3 vcl_procurement/tests/test_rules.py

The rules, in the order Tanuj decided them (29-30 Sep 2026):

  * An IMPORT invoice is posted (ledger date) on the KRA CUSTOMS ENTRY DATE, not
    the supplier's invoice date - VAT is recognised on the entry date. The due
    date still runs from the supplier invoice date plus terms.
  * An IMPORT invoice stays a draft until three things are filled: the supplier
    invoice date, the KRA customs entry date and the KRA customs entry number.
  * An import payment schedule is two rows: the net total, due per the supplier's
    payment terms counted from the bill date; and the taxes, due N days (default
    2) after the vessel lands (expected delivery to port).
  * Taxes are shown and typed in KES. On a foreign-currency document ERPNext
    stores the document-currency figure, which is the KES amount / the rate.
  * A line is bought in any unit; the conversion to the item's stock unit is the
    number of stock units in one of the chosen unit.
"""

import calendar
import math
import datetime
import html as _html
import re

IMPORT = "Importation"
COMPANY_CURRENCY = "KES"
DEFAULT_TAX_DAYS_AFTER_LANDING = 2
# Import taxes fall due on the EARLIER of N days after landing and this many days from the supplier invoice date (Tanuj, 08/10/2026, TRK-00147).
TAX_DAYS_FROM_INVOICE = 30
# The Payment Term on the Taxes row. Without a term a payment cannot be credited to the row (ERPNext matches by term).
IMPORT_TAXES_TERM = "Import Taxes"

CHARGE_TYPES = ["Actual", "On Net Total", "On Previous Row Amount",
                "On Previous Row Total", "On Item Quantity"]
PREVIOUS_ROW_TYPES = {"On Previous Row Amount", "On Previous Row Total"}


class RuleError(Exception):
    """A rule refused the input. The message is written for the person using the screen."""


# --- dates -------------------------------------------------------------------

def to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return datetime.date.fromisoformat(str(v)[:10])


def iso(v):
    d = to_date(v)
    return d.isoformat() if d else None


def loose_date(v):
    """A date as other systems write it: 2026-10-27, 27/10/2026, 27-10-2026, 27-Oct-2026, with or without a time.

    None when it cannot be read. KRA's iTax answer is not always ISO.
    """
    if v is None:
        return None
    if isinstance(v, (datetime.date, datetime.datetime)):
        return to_date(v)
    t = str(v).strip()
    if not t:
        return None
    head = t.replace("T", " ").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(head if fmt != "%d %b %Y" else t[:11], fmt).date()
        except ValueError:
            continue
    return None


# --- the bill date against KRA's invoice date (CR-010, policy from 31/10/2026) ------

def bill_date_review(kra_date, bill_date):
    """Is the bill date acceptable against the supplier invoice date KRA holds for the CUIN?

    VCL's rule (Tanuj, 31/10/2026): a bill date later than KRA's date is accepted when both are in
    the same calendar month and year. A different month or year needs the bill date reviewed before
    the bill is pushed. An earlier bill date is not covered by the rule, so it is reviewed too.
    Returns {state, ok, review, kra_date, bill_date, text}; state is one of same_day, same_month,
    cross_month, earlier, unverified.
    """
    k, b = loose_date(kra_date), loose_date(bill_date)
    out = {"kra_date": iso(k), "bill_date": iso(b)}
    if not k or not b:
        missing = "KRA's invoice date" if not k else "the bill date"
        return {**out, "state": "unverified", "ok": False, "review": False,
                "text": f"Date not checked: {missing} is not known."}
    if k == b:
        return {**out, "state": "same_day", "ok": True, "review": False, "text": "Bill date matches KRA's invoice date."}
    if b < k:
        return {**out, "state": "earlier", "ok": False, "review": True,
                "text": "Bill date is before KRA's invoice date: review the bill date."}
    if (b.year, b.month) == (k.year, k.month):
        return {**out, "state": "same_month", "ok": True, "review": False, "text": "Same-month late posting accepted."}
    return {**out, "state": "cross_month", "ok": False, "review": True,
            "text": "Cross-month date difference: review the bill date before pushing."}


def dmy(v):
    """dd/mm/yyyy, as the screens show dates. '' when there is none."""
    d = to_date(v)
    return d.strftime("%d/%m/%Y") if d else ""


def add_days(v, n):
    return iso(to_date(v) + datetime.timedelta(days=int(n)))


def end_of_month(v):
    d = to_date(v)
    return iso(datetime.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1]))


def add_months_end(v, n):
    """The last day of the month n months after v's month."""
    d = to_date(v)
    idx = d.year * 12 + (d.month - 1) + int(n)
    y, m = divmod(idx, 12)
    return iso(datetime.date(y, m + 1, calendar.monthrange(y, m + 1)[1]))


def term_due_date(bill_date, term):
    """Due date for one payment term, counted from the supplier's invoice date.

    `term` carries ERPNext's own fields: due_date_based_on, credit_days,
    credit_months. Read from the Payment Term / template row - never from its
    NAME: the term called "150 Days" is really 145.
    """
    if not bill_date or not term:
        return None
    base = str(term.get("due_date_based_on") or "")
    days = int(term.get("credit_days") or 0)
    months = int(term.get("credit_months") or 0)
    if re.search(r"end of the invoice month", base, re.I):
        if re.search(r"month\(s\)", base, re.I):
            return add_months_end(bill_date, months)
        return add_days(end_of_month(bill_date), days)
    if re.search(r"month\(s\) after invoice", base, re.I):
        return add_months_end(bill_date, months)
    return add_days(bill_date, days)


def rebase_due_dates(rows, bill_date):
    """Re-count every payment-schedule row's due date from the supplier's invoice date.

    An invoice mapped from a purchase order counts its due dates from TODAY (or the PO date);
    the supplier's terms count from the invoice date. Left alone, the due date ends up later than
    ERPNext allows and the insert is refused with "Due Date cannot be after ...". Returns
    (new_due_dates_in_row_order, the_invoice_due_date = the latest row). Nothing to do without a
    bill date or rows: both come back None and the caller leaves the invoice as it is.
    """
    if not bill_date or not rows:
        return None, None
    dues = [term_due_date(bill_date, r) for r in rows]
    known = [d for d in dues if d]
    return dues, (max(known) if known else None)


def tax_due_date(expected_delivery_to_port, days_after=DEFAULT_TAX_DAYS_AFTER_LANDING, bill_date=None):
    """Earlier of (days_after the vessel lands) and (30 days from the supplier invoice date).

    The landing date is still required: without it the due date is unknown, and the invoice-date
    cap alone is only an upper bound. ISO date strings compare correctly as text."""
    if not expected_delivery_to_port:
        return None
    due = add_days(expected_delivery_to_port, days_after)
    if bill_date:
        cap = add_days(bill_date, TAX_DAYS_FROM_INVOICE)
        if str(cap) < str(due):
            return cap
    return due


# --- currency ----------------------------------------------------------------

def kes_to_doc(kes, rate, currency):
    """A KES amount as ERPNext stores it: document currency, i.e. KES / rate."""
    kes = float(kes or 0)
    if currency == COMPANY_CURRENCY:
        return kes
    rate = float(rate or 0)
    if rate <= 0:
        raise RuleError("The exchange rate must be greater than zero.")
    return kes / rate


def doc_to_kes(amount, rate, currency):
    amount = float(amount or 0)
    if currency == COMPANY_CURRENCY:
        return amount
    return amount * float(rate or 1)


# --- import invoice: posting date and the submit gate -------------------------

def is_import(invoice_type):
    return (invoice_type or "").strip() == IMPORT


def posting_date_for(invoice_type, kra_entry_date, current_posting_date):
    """The ledger date. Imports post on the KRA customs entry date; nothing else moves."""
    if is_import(invoice_type) and kra_entry_date:
        return iso(kra_entry_date)
    return iso(current_posting_date)


def kra_gate_missing(invoice_type, bill_date, kra_entry_date, kra_entry_number):
    """What an import invoice still needs before it may be submitted. [] = ready."""
    if not is_import(invoice_type):
        return []
    missing = []
    if not bill_date:
        missing.append("Supplier invoice date (bill date)")
    if not kra_entry_date:
        missing.append("KRA customs entry date")
    if not (kra_entry_number or "").strip():
        missing.append("KRA customs entry number")
    return missing


def pending_before_submit(invoice_type, bill_date, kra_entry_date, kra_entry_number, bill_no):
    """What ANY invoice still needs before it may be submitted from the screen. [] = ready.

    An import needs its three KRA details. A local invoice needs the supplier invoice date
    and the supplier invoice number. (The Desk hook stays import-only: see `kra_gate_missing`.)
    """
    if is_import(invoice_type):
        return kra_gate_missing(invoice_type, bill_date, kra_entry_date, kra_entry_number)
    missing = []
    if not bill_date:
        missing.append("Supplier invoice date (bill date)")
    if not (bill_no or "").strip():
        missing.append("Supplier invoice no.")
    return missing


def local_schedule(total, bill_date, term, term_name):
    """The payment schedule of a LOCAL invoice: one row, the whole invoice, due per the
    supplier's terms counted from the supplier invoice date. Returns (rows, missing)."""
    total = round(float(total or 0), 2)
    missing = []
    due = term_due_date(bill_date, term) if term else None
    if not term:
        missing.append("payment terms")
    elif not due:
        missing.append("supplier invoice date (bill date)")
    if missing:
        return None, missing
    return [{"payment_term": term_name, "description": "Total: paid as per supplier terms",
             "due_date": due, "invoice_portion": 100.0, "payment_amount": total}], []


def pending_lines(items):
    """What is still to happen on a purchase order, worked out from its lines.

    Returns {"to_receive": [{"uom", "qty"}], "to_bill": amount before tax}. ERPNext's own
    status ("To Receive and Bill") says neither how much nor of what, so the screen says both.
    """
    by_uom, to_bill = {}, 0.0
    for i in items:
        left = float(i.get("qty") or 0) - float(i.get("received_qty") or 0)
        if left > 1e-9:
            u = i.get("uom") or ""
            by_uom[u] = by_uom.get(u, 0.0) + left
        to_bill += max(0.0, float(i.get("amount") or 0) - float(i.get("billed_amt") or 0))
    return {"to_receive": [{"uom": u, "qty": round(q, 6)} for u, q in by_uom.items()],
            "to_bill": round(to_bill, 2)}


def match_material_requests(lines, open_rows):
    """Which unlinked PO lines belong to an open material request: [(line index, row)].

    A line keeps its request only if it was started from the request's own "Use Lines".
    Swap the item, or copy the supplier's last order, and the link was gone, so the
    request sat at 0% ordered with the goods already on order (MAT-MR-2026-00079 /
    PUR-ORD-2026-00270, 28/10/2026). Linking every PO line through this is why that
    can't happen again, whichever screen raised it.

    `lines`: [{item_code, stock_qty, material_request?, material_request_item?}].
    `open_rows`: [{material_request, material_request_item, item_code, pending}],
    with `pending` in stock units. A line is linked only when exactly ONE open row for
    its item still has room for its whole quantity. Two requests for the same item is a
    buyer's call, not a guess.
    """
    room = {r["material_request_item"]: float(r.get("pending") or 0) for r in open_rows}
    for ln in lines:                     # explicit links use up their row first
        mri = ln.get("material_request_item")
        if mri in room:
            room[mri] -= float(ln.get("stock_qty") or 0)
    out = []
    for i, ln in enumerate(lines):
        if ln.get("material_request") or not ln.get("item_code"):
            continue
        qty = float(ln.get("stock_qty") or 0)
        if qty <= 0:
            continue
        rows = [r for r in open_rows if r["item_code"] == ln["item_code"]
                and room[r["material_request_item"]] > 1e-9]
        if len(rows) != 1 or room[rows[0]["material_request_item"]] + 1e-9 < qty:
            continue
        room[rows[0]["material_request_item"]] -= qty
        out.append((i, rows[0]))
    return out


def po_open_for_receipt(docstatus, status, per_received):
    """(ok, why) - can goods still be received against this purchase order?"""
    if docstatus != 1:
        return False, "The PO is not approved"
    if status in ("Closed", "Cancelled", "Completed") or float(per_received or 0) >= 100:
        return False, "Already received" if float(per_received or 0) >= 100 else f"The PO is {status}"
    return True, ""


# --- import payment schedule --------------------------------------------------

def import_schedule(net, taxes, bill_date, term, term_name, expected_delivery_to_port,
                    tax_days_after_landing=DEFAULT_TAX_DAYS_AFTER_LANDING):
    """The two-row schedule for an import invoice, in document currency.

    Returns (rows, missing). `missing` lists what could not be worked out, in
    words; rows are only returned when nothing is missing, so a half-known
    schedule is never saved.
    """
    net = round(float(net or 0), 2)
    taxes = round(float(taxes or 0), 2)
    grand = net + taxes
    missing = []
    net_due = term_due_date(bill_date, term) if term else None
    if not term:
        missing.append("payment terms")
    elif not net_due:
        missing.append("supplier invoice date (bill date)")
    tax_due = tax_due_date(expected_delivery_to_port, tax_days_after_landing, bill_date)
    if not tax_due:
        missing.append("expected delivery to port")
    if missing:
        return None, missing
    # ERPNext recomputes each row's amount from its PORTION on save, so a portion rounded
    # to 2 dp moves the split (0.67 USD on a 50k invoice). Keep it at full precision.
    portion = round(net / grand * 100, 8) if grand else 100.0
    return [
        {"payment_term": term_name, "description": "Net total: paid as per supplier terms",
         "due_date": net_due, "invoice_portion": portion, "payment_amount": net},
        {"payment_term": IMPORT_TAXES_TERM,
         "description": f"Taxes: due the earlier of {int(tax_days_after_landing)} days after the vessel lands or {TAX_DAYS_FROM_INVOICE} days from the supplier invoice date",
         "due_date": tax_due, "invoice_portion": round(100 - portion, 8), "payment_amount": taxes},
    ], []


# --- units --------------------------------------------------------------------

def uom_factor(uom, stock_uom, own_rows, global_factor_to_stock=None, global_factor_from_stock=None):
    """Stock units in ONE of `uom`, or None when nothing knows.

    Order of trust: the stock unit itself (1), the item's own conversion rows,
    then the site-wide UOM Conversion Factor table read either way round.
    """
    if uom == stock_uom:
        return 1.0
    for r in own_rows or []:
        if r.get("uom") == uom and float(r.get("conversion_factor") or 0) > 0:
            return float(r["conversion_factor"])
    if global_factor_to_stock and float(global_factor_to_stock) > 0:
        return float(global_factor_to_stock)
    if global_factor_from_stock and float(global_factor_from_stock) > 0:
        return 1.0 / float(global_factor_from_stock)
    return None


def check_line_units(lines, stock_uoms):
    """A unit other than the stock unit must arrive with its conversion."""
    for ln in lines:
        stock = stock_uoms.get(ln.get("item_code"))
        uom = ln.get("uom") or stock
        if stock and uom != stock and not float(ln.get("conversion_factor") or 0) > 0:
            raise RuleError(
                f"Enter the conversion for {uom} on {ln.get('item_name') or ln.get('item_code')} "
                f"(how many {stock} in 1 {uom}).")


# --- search -------------------------------------------------------------------

def tokens_of(q):
    """Words to search for. Spaces, % and * separate words; order does not matter."""
    return [t for t in re.split(r"[\s%*]+", str(q or "").lower()) if t]


def matches(haystack, toks):
    h = str(haystack or "").lower()
    return all(t in h for t in toks)


# --- taxes --------------------------------------------------------------------

def check_tax_rows(rows):
    """Refuse a tax row ERPNext would reject or, worse, quietly mis-calculate."""
    for i, r in enumerate(rows, start=1):
        ct = r.get("charge_type")
        if ct not in CHARGE_TYPES:
            raise RuleError(f"Tax row {i} has an unknown type: {ct}.")
        if ct in PREVIOUS_ROW_TYPES:
            rid = int(r.get("row_id") or 0)
            if rid < 1 or rid >= i:
                raise RuleError(f"Tax row {i} must be calculated on an earlier row (row number below {i}).")
        if r.get("add_deduct_tax") not in (None, "Add", "Deduct"):
            raise RuleError(f"Tax row {i}: choose Add or Deduct.")
    return rows


def signed_sum(rows, key):
    return sum((-1 if r.get("add_deduct_tax") == "Deduct" else 1) * float(r.get(key) or 0) for r in rows)


# --- receiving goods against a PO ---------------------------------------------

def allowance_pct(item_pct, global_pct):
    """ERPNext's over-receipt allowance: the item's own % when it has one, else Stock Settings'."""
    item_pct = float(item_pct or 0)
    return item_pct if item_pct > 0 else float(global_pct or 0)


def max_receipt_qty(pending, pct):
    """The most that can be received on a line: pending plus the allowance, cut (never rounded up) at 6 dp."""
    pending = max(0.0, float(pending or 0))
    return math.floor(pending * (1 + float(pct or 0) / 100) * 1e6 + 1e-6) / 1e6


def start_receipt_qty(pending, max_qty, invoice_qty=None):
    """What a line is pre-filled with: everything pending, or - opened from an invoice - what the invoice
    bills, held to the most that may be received. A line the invoice does not bill starts at 0."""
    if invoice_qty is None:
        return float(pending or 0)
    return min(float(invoice_qty or 0), float(max_qty or 0))


def invoice_qty_by_line(po_rows, invoice_rows):
    """{PO row idx: qty billed on the invoice}, matched on the invoice row's po_detail = the PO row's name.
    Both are lists of dicts; PO rows carry `name` and `idx`, invoice rows `po_detail` and `qty`."""
    idx_of = {r.get("name"): r.get("idx") for r in po_rows}
    out = {}
    for r in invoice_rows:
        idx = idx_of.get(r.get("po_detail"))
        if idx is not None:
            out[idx] = out.get(idx, 0.0) + float(r.get("qty") or 0)
    return out


def check_receipt_lines(wanted, limits):
    """Refuse a receipt line above what ERPNext will accept. `wanted` and `limits` are {idx: qty}."""
    if not any(float(v or 0) > 0 for v in wanted.values()):
        raise RuleError("No quantities to receive.")
    for idx, qty in wanted.items():
        qty = float(qty or 0)
        if qty <= 0:
            continue
        mx = limits.get(idx)
        if mx is None:
            raise RuleError(f"Line {idx} is not on the purchase order.")
        if qty > mx + 1e-9:
            raise RuleError(f"Line {idx}: {qty:g} is more than can be received (at most {mx:g}, the order quantity still pending plus the over-receipt allowance).")


def unbilled_lines(po_rows):
    """PO rows with something left to bill: [{idx, left}] where left = amount - billed_amt, > 0."""
    out = []
    for r in po_rows:
        left = float(r.get("amount") or 0) - float(r.get("billed_amt") or 0)
        if left > 0.005:
            out.append({"idx": r.get("idx"), "left": round(left, 2)})
    return out


# --- invoice type follows the order ----------------------------------------------

def invoice_type_for_po(order_type, tax_category):
    """The purchase invoice type implied by the Purchase Order it bills.

    An import order (order type Import, or the Importation tax category) bills as
    Importation; any other order that says what it is bills as Local Purchase; an order
    that says nothing implies nothing (None), so the invoice keeps what it has.
    """
    if (order_type or "").strip() == "Import" or (tax_category or "").strip() == IMPORT:
        return IMPORT
    if (order_type or "").strip() or (tax_category or "").strip():
        return "Local Purchase"
    return None


# --- descriptions: Item / PO rows hold HTML, the screens edit plain text -------------

def plain_text(html):
    """HTML description -> plain text. Line breaks survive; every tag and entity goes."""
    s = str(html or "")
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<\s*/\s*(p|div|li)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]*>", "", s)
    for a, b in (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#x27;", "'"), ("&#39;", "'"), ("&amp;", "&")):
        s = s.replace(a, b)          # &amp; last, so "&amp;lt;" stays "&lt;"
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def html_from_text(text):
    """Plain text -> the editor markup ERPNext's text editor writes, one <p> per line."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    body = "".join("<p>%s</p>" % _html.escape(ln, quote=False) if ln else "<p><br></p>" for ln in lines)
    return '<div class="ql-editor read-mode">%s</div>' % body


def is_stale(due_iso, today_iso, days=90):
    """True when the due date is more than `days` days before today."""
    if not due_iso or not today_iso:
        return False
    return (to_date(today_iso) - to_date(due_iso)).days > int(days)


def check_order_date(order_date, required_by):
    """An order cannot be dated after the day it is required. Blank on either side passes."""
    if order_date and required_by and to_date(order_date) > to_date(required_by):
        raise RuleError("The order date cannot be after the required-by date.")


def first_line_text(item_name, description):
    """What a list row shows as the first thing ordered: the description when it says more than
    the item name, else the name. Whitespace is collapsed to single spaces."""
    d = re.sub(r"\s+", " ", plain_text(description)).strip()
    n = re.sub(r"\s+", " ", str(item_name or "")).strip()
    return d if d and d != n else n


def ordered_summary(lines):
    """{text, more} for a list row from its ordered lines [{item_name, description}] in order."""
    if not lines:
        return {"text": "", "more": 0}
    first = lines[0]
    return {"text": first_line_text(first.get("item_name"), first.get("description")), "more": len(lines) - 1}


def rate_after_lookup(current, looked_up):
    """The exchange rate to keep: the looked-up one when it is usable (> 0), else the document's own."""
    try:
        v = float(looked_up or 0)
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else float(current or 0)


# --- QuickBooks bill: the number and the checks before a push ------------------------

QBO_DOCNUMBER_LIMIT = 21


def qbo_docnumber(invoice_type, kra_entry_no, bill_no, override=None):
    """The bill number QuickBooks will carry, and where it came from.

    An import is posted under its KRA customs entry number, not the supplier's invoice number; a local purchase
    keeps the supplier's invoice number. A number typed on the queue row wins over both.
    """
    o = (override or "").strip()
    if o:
        return o, "typed on the queue row"
    if is_import(invoice_type):
        return (kra_entry_no or "").strip(), "KRA customs entry number"
    return (bill_no or "").strip(), "supplier invoice number"


def qbo_txn_date(invoice_type, kra_entry_date, bill_date, posting_date):
    """The date QuickBooks carries on the bill. An import is dated on its KRA customs entry date, nothing else;
    a local purchase on the supplier's bill date, falling back to the ledger date."""
    if is_import(invoice_type):
        return kra_entry_date or None
    return bill_date or posting_date or None


def qbo_checks(docnumber, txn_date, vendor, lines, pi_net, category, tolerance=0.01):
    """What must be true before a bill can be pushed. Each check: {ok, label, why}. `vendor` is {name, approved} or None.

    A line is routed when it names a QuickBooks item or account. `lines` are dicts with amount, item, account.
    """
    out = []
    n = (docnumber or "").strip()
    out.append({"ok": bool(n) and len(n) <= QBO_DOCNUMBER_LIMIT, "label": "Bill number",
                "why": "missing" if not n else f"{len(n)} characters; QuickBooks allows {QBO_DOCNUMBER_LIMIT}" if len(n) > QBO_DOCNUMBER_LIMIT else n})
    out.append({"ok": bool(txn_date), "label": "Bill date", "why": "missing" if not txn_date else str(txn_date)})
    v_ok = bool(vendor and vendor.get("name") and vendor.get("approved"))
    out.append({"ok": v_ok, "label": "Vendor in QuickBooks",
                "why": (vendor or {}).get("name") if v_ok else "no approved vendor mapping" if not vendor or not vendor.get("name") else "mapping not approved"})
    unrouted = [str(i + 1) for i, l in enumerate(lines) if not (l.get("item") or l.get("account"))]
    out.append({"ok": bool(lines) and not unrouted, "label": "Every line has an item or account",
                "why": "no lines on the queue row" if not lines else ("line " + ", ".join(unrouted) + " not routed") if unrouted else f"{len(lines)} lines"})
    total = round(sum(float(l.get("amount") or 0) for l in lines), 2)
    out.append({"ok": bool(lines) and abs(total - float(pi_net or 0)) <= tolerance, "label": "Lines add up to the invoice net",
                "why": f"lines {total:,.2f}, invoice {float(pi_net or 0):,.2f}"})
    out.append({"ok": category != "BLOCKED", "label": "Staging", "why": "blocked at staging" if category == "BLOCKED" else ("staged · " + category if category else "staged")})
    return out


def stage_hint(staging_enabled, earliest_posting_date, posting_date):
    """Why a submitted invoice was not queued for QuickBooks on submit (DEF-001). None when the settings would have queued it."""
    if not staging_enabled:
        return "Automatic staging on submit is switched off (QBO Sync Settings), so use Stage for QBO."
    cut, post = to_date(earliest_posting_date), to_date(posting_date)
    if cut and post and post < cut:
        return f"The posting date {dmy(post)} is before the QuickBooks cutoff {dmy(cut)}, so it is never queued."
    return "It should have been queued on submit; the Error Log may say why. Use Stage for QBO."


# --- receiving: which receipt row is which PO line (27/10/2026) -------------------

def receipt_qty_by_row(wanted, po_rows, pr_rows):
    """The quantity for each row of a receipt mapped from a PO, or None to drop the row.

    `wanted` is {PO line idx: qty}, as every client sends it. The mapped receipt holds only
    the PENDING lines and numbers them again from 1, so its own `idx` says nothing about which
    PO line a row is: with line 1 already received, receipt row 1 is PO line 2. The row's
    `purchase_order_item` (the PO row's name) is the only safe key. `po_rows` carry `name` and
    `idx`; `pr_rows` carry `purchase_order_item`.
    """
    idx_of = {r.get("name"): r.get("idx") for r in po_rows}
    out = []
    for r in pr_rows:
        idx = idx_of.get(r.get("purchase_order_item"))
        qty = wanted.get(int(idx)) if idx is not None else None
        out.append(float(qty) if qty is not None and float(qty) > 0 else None)
    return out


# --- payment schedule follows the invoice total (27/10/2026) -----------------------

def refit_schedule(rows, net, grand, is_import):
    """The saved schedule re-fitted to a changed invoice total, or None when it needs nothing.

    ERPNext keeps each row's PORTION and scales the amount from it, so after a line or tax edit
    an import's "net" row stops being the net total and the "taxes" row stops being the taxes.
    An import's two-row schedule (net, then taxes) gets its amounts back; a single row becomes the
    whole invoice. Any other shape is left to ERPNext. Due dates are never touched.
    """
    net, grand = round(float(net or 0), 2), round(float(grand or 0), 2)
    if not rows or grand <= 0:
        return None
    if is_import and len(rows) == 2:
        portion = round(net / grand * 100, 8)
        want = [(portion, net), (round(100 - portion, 8), round(grand - net, 2))]
    elif len(rows) == 1:
        want = [(100.0, grand)]
    else:
        return None
    same = all(abs(float(r.get("payment_amount") or 0) - amt) < 0.005 and abs(float(r.get("invoice_portion") or 0) - por) < 1e-6
               for r, (por, amt) in zip(rows, want))
    if same:
        return None
    return [{**r, "invoice_portion": por, "payment_amount": amt} for r, (por, amt) in zip(rows, want)]


# --- references every bill carries (27/10/2026) --------------------------------------

def file_no(po_file, pi_file, proforma):
    """(file number, where it came from). The PO's own, else the invoice's, else the proforma
    (the reference the PO stands in when it has none of its own; resolved on read, never copied)."""
    for v, src in ((po_file, "the PO"), (pi_file, "this invoice"), (proforma, "the proforma (the PO has no file no.)")):
        if str(v or "").strip():
            return str(v).strip(), src
    return None, None


def qbo_memo(pi_name, bill_no, pos, proforma=None, file_number=None, bl_no=None, containers=(), currency=None, total=None,
             rate=None, rate_date=None):
    """The QuickBooks memo for ANY purchase invoice: the references a person needs to find this bill
    again from QuickBooks - the order, the supplier's invoice and the ERPNext invoice - plus, on an
    import, the BL and containers. Short enough for QuickBooks' 4000-character private note.

    `total` is the NET amount, before taxes (cr-mvqewpg9). On a foreign-currency invoice the
    exchange rate it was booked at follows it, with the invoice date: "USD 103,115.60 net · rate 130.05 on 14/09/2026"."""
    pos = [p for p in (pos or []) if p]
    conts = [str(c).strip() for c in (containers or []) if str(c or "").strip()]
    parts = [
        "BL " + str(bl_no).strip() if str(bl_no or "").strip() else "",
        "Containers " + ", ".join(conts) if conts else "",
        "Supplier inv " + str(bill_no).strip() if str(bill_no or "").strip() else "",
        ("Order " + ", ".join(pos) + (f" (proforma {proforma})" if proforma else "")) if pos else (f"Proforma {proforma}" if proforma else ""),
        "File no " + str(file_number).strip() if str(file_number or "").strip() else "",
        "ERPNext " + pi_name if pi_name else "",
        f"{currency} {float(total or 0):,.2f} net" if currency else "",
        _memo_rate(currency, rate, rate_date),
    ]
    return " · ".join(p for p in parts if p)[:4000]


def _memo_rate(currency, rate, rate_date):
    """"rate 130.05 on 14/09/2026" for a foreign-currency invoice; "" for KES or no rate."""
    try:
        r = float(rate or 0)
    except (TypeError, ValueError):
        r = 0
    if not currency or str(currency).upper() == "KES" or r <= 0 or abs(r - 1) < 1e-9:
        return ""
    d = iso(rate_date) if rate_date else ""
    on = f" on {d[8:10]}/{d[5:7]}/{d[:4]}" if len(d) >= 10 else ""
    return f"rate {r:,.4f}".rstrip("0").rstrip(".") + on


def memo_has_refs(memo, pi_name, pos):
    """Does a memo already carry the order and the ERPNext invoice? A typed memo that does is kept."""
    m = str(memo or "")
    return bool(m.strip()) and all(p in m for p in (pos or [])) and (not pi_name or pi_name in m)


# --- the shipment record an import invoice belongs to (27/10/2026) -----------------

def norm_ref(v):
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def pick_shipment(shipments, bl_no, pos):
    """The Import Shipment an invoice belongs to, or None.

    A BL is one voyage, so a BL match wins. Without one, the order's shipment - but only when the
    order has a single one: a split order has a shipment per voyage and guessing between them would
    put this invoice's vessel on the wrong one. `shipments` carry name, bl_number, purchase_order."""
    bl = norm_ref(bl_no)
    if bl:
        for s in shipments:
            if norm_ref(s.get("bl_number")) == bl:
                return s
    mine = [s for s in shipments if s.get("purchase_order") in set(pos or [])]
    return mine[0] if len(mine) == 1 else None
