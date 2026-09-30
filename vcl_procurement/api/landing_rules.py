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
