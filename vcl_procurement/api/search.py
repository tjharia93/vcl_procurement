"""Searching for what can be bought, and the units it can be bought in.

Replaces the two searches in `raise_po.py`, which matched one fixed string, so
`ncr cfb whi` found nothing for an item called `NCR-250-55-WHI-CB`. Here every
word must match, in any order, against the code, the name or the group; `%`
and spaces both separate words.

Never creates an item. A supplier quoting something that is not in the item
master is a master-data decision, made in the item master.
"""

import frappe
from frappe.query_builder import DocType

from vcl_procurement.api import rules
from vcl_procurement.api.purchasing import _assert_purchasing_role

MAX_RESULTS = 25


def _like_any(table, fields, tok):
    cond = None
    for f in fields:
        c = table[f].like(f"%{tok}%")
        cond = c if cond is None else (cond | c)
    return cond


@frappe.whitelist()
def search_items(q="", limit=MAX_RESULTS):
    """Items that can be bought. Every word must match code, name or group."""
    _assert_purchasing_role()
    toks = rules.tokens_of(q)
    if not toks:
        return []
    item = DocType("Item")
    query = (frappe.qb.from_(item)
             .select(item.name, item.item_name, item.stock_uom, item.item_group, item.last_purchase_rate)
             .where(item.disabled == 0).where(item.is_purchase_item == 1))
    for t in toks:
        query = query.where(_like_any(item, ["name", "item_name", "item_group"], t))
    first = toks[0]
    rows = query.orderby(item.item_name).limit(200).run(as_dict=True)
    # Codes that start with the first word come first, then shorter codes.
    rows.sort(key=lambda r: (0 if r.name.lower().startswith(first) else 1, len(r.name)))
    return [{
        "item_code": r.name,
        "item_name": r.item_name or r.name,
        "uom": r.stock_uom,
        "item_group": r.item_group,
        # A hint only - the buyer types the rate they were quoted.
        "last_rate": float(r.last_purchase_rate or 0),
    } for r in rows[: min(int(limit or MAX_RESULTS), 50)]]


@frappe.whitelist()
def search_suppliers(q="", limit=MAX_RESULTS):
    _assert_purchasing_role()
    toks = rules.tokens_of(q)
    if not toks:
        return []
    sup = DocType("Supplier")
    query = (frappe.qb.from_(sup)
             .select(sup.name, sup.supplier_name, sup.supplier_group, sup.default_currency,
                     sup.payment_terms, sup.tax_category)
             .where(sup.disabled == 0))
    for t in toks:
        query = query.where(_like_any(sup, ["name", "supplier_name", "supplier_group"], t))
    rows = query.orderby(sup.name).limit(50).run(as_dict=True)
    return [{
        "name": r.name,
        "label": r.supplier_name or r.name,
        "group": r.supplier_group,
        "currency": r.default_currency,
        "payment_terms": r.payment_terms,
        "tax_category": r.tax_category,
    } for r in rows[: min(int(limit or MAX_RESULTS), 50)]]


@frappe.whitelist()
def uom_list():
    """Every enabled unit, for the type-to-search unit box."""
    _assert_purchasing_role()
    return frappe.get_all("UOM", filters={"enabled": 1}, pluck="name", order_by="name asc",
                          limit_page_length=1000)


@frappe.whitelist()
def uom_factor(item_code, uom):
    """How many of the item's stock unit are in ONE `uom`. `factor` is null when
    neither the item nor the site conversion table knows: the buyer then types it."""
    _assert_purchasing_role()
    if not frappe.db.exists("Item", item_code):
        frappe.throw(f"Item {item_code} was not found.")
    item = frappe.get_doc("Item", item_code)
    stock = item.stock_uom
    own = [{"uom": u.uom, "conversion_factor": u.conversion_factor} for u in item.get("uoms") or []]

    def _g(f, t):
        v = frappe.db.get_value("UOM Conversion Factor", {"from_uom": f, "to_uom": t}, "value")
        return float(v) if v else None

    factor = rules.uom_factor(uom, stock, own, _g(uom, stock), _g(stock, uom))
    return {"item_code": item_code, "stock_uom": stock, "uom": uom, "factor": factor}
