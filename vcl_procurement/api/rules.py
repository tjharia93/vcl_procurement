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


def tax_due_date(expected_delivery_to_port, days_after=DEFAULT_TAX_DAYS_AFTER_LANDING):
    if not expected_delivery_to_port:
        return None
    return add_days(expected_delivery_to_port, days_after)


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
    tax_due = tax_due_date(expected_delivery_to_port, tax_days_after_landing)
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
        {"payment_term": None,
         "description": f"Taxes: due {int(tax_days_after_landing)} days after the vessel lands",
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
