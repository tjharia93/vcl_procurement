"""Pure helpers for the procurement landing page. Nothing here imports frappe, so
`tests/test_landing.py` runs without a bench.

    python3 vcl_procurement/tests/test_landing.py
"""
import re

MIN_CONTAINER_CHARS = 3
LIMIT = 400


def norm_container(value):
    """MSKU 9594207 / msku-9594207 / 'MSKU9594207 ' -> MSKU9594207."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def container_matches(reference, query):
    """A container reference matches when the normalised query is inside it. Too-short queries match nothing."""
    q = norm_container(query)
    return len(q) >= MIN_CONTAINER_CHARS and q in norm_container(reference)


def bl_matches(bl_no, query):
    """A bill of lading matches on the same normalisation as a container (upper-case, A-Z0-9 only,
    substring, minimum length)."""
    return container_matches(bl_no, query)


def bl_hits(invoices, query):
    """The invoice rows (dicts carrying `custom_bill_of_lading_number`) whose BL matches, cancelled ones dropped."""
    return [i for i in invoices or []
            if i.get("docstatus") != 2 and bl_matches(i.get("custom_bill_of_lading_number"), query)]


def group_pos(child_rows):
    """{parent: [purchase orders...]} from child rows carrying `parent` and `purchase_order`, sorted, no blanks or repeats."""
    out = {}
    for r in child_rows or []:
        po = r.get("purchase_order")
        if po:
            out.setdefault(r.get("parent"), set()).add(po)
    return {k: sorted(v) for k, v in out.items()}


def group_items(child_rows, keys=("item_name", "qty", "uom")):
    """{parent: [ {key: value...} ]} keeping row order."""
    out = {}
    for r in child_rows or []:
        out.setdefault(r.get("parent"), []).append({k: r.get(k) for k in keys})
    return out


def is_open_mr(row):
    """A material request still needing an order."""
    return float(row.get("per_ordered") or 0) < 100 and row.get("status") not in ("Stopped", "Cancelled")
