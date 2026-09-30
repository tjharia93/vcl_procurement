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
import datetime
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
