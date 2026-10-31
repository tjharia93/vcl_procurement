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

from vcl_procurement.api import landing_rules, rules
from vcl_procurement.api.purchasing import _assert_purchasing_role
from vcl_procurement.api.units import resolve_factor

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
             .select(item.name, item.item_name, item.stock_uom, item.item_group, item.last_purchase_rate,
                     item.description)
             .where(item.disabled == 0).where(item.is_purchase_item == 1))
    for t in toks:
        query = query.where(_like_any(item, ["name", "item_name", "item_group"], t))
    first = toks[0]
    rows = query.orderby(item.item_name).limit(400).run(as_dict=True)
    # Codes that start with the first word come first, then shorter codes.
    rows.sort(key=lambda r: (0 if r.name.lower().startswith(first) else 1, len(r.name)))
    return [{
        "item_code": r.name,
        "item_name": r.item_name or r.name,
        "uom": r.stock_uom,
        "item_group": r.item_group,
        # A hint only - the buyer types the rate they were quoted.
        "last_rate": float(r.last_purchase_rate or 0),
        # Item.description is HTML; the screen shows and edits plain text.
        "description": rules.plain_text(r.description),
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
    stock, factor = resolve_factor(item_code, uom)
    return {"item_code": item_code, "stock_uom": stock, "uom": uom, "factor": factor}


# --- one Purchasing search (CR-002, 31/10/2026) -------------------------------------

# kind -> (doctype, child doctype, header fields, reference fields, supplier field, date field, filters)
_DOC_SEARCH = {
    "mr": ("Material Request", "Material Request Item", ["name", "status", "transaction_date", "owner"],
           ["name"], None, "transaction_date", {"docstatus": ["<", 2], "material_request_type": "Purchase"}),
    "po": ("Purchase Order", "Purchase Order Item", ["name", "supplier", "status", "transaction_date", "order_confirmation_no", "custom_file_number"],
           ["name", "order_confirmation_no", "custom_file_number"], "supplier", "transaction_date", {"docstatus": ["<", 2]}),
    "pr": ("Purchase Receipt", "Purchase Receipt Item", ["name", "supplier", "supplier_name", "status", "posting_date", "supplier_delivery_note"],
           ["name", "supplier_delivery_note"], "supplier_name", "posting_date", {"docstatus": ["<", 2], "is_return": 0}),
    "pi": ("Purchase Invoice", "Purchase Invoice Item", ["name", "supplier", "supplier_name", "status", "posting_date", "bill_no", "custom_bill_of_lading_number"],
           ["name", "bill_no", "custom_bill_of_lading_number"], "supplier_name", "posting_date", {"docstatus": ["<", 2], "is_return": 0}),
}
_SEARCH_WINDOW = 600     # the newest documents of each type that are searched
_LABEL = {"mr": "Material Request", "po": "Purchase Order", "pr": "Receiving", "pi": "Purchase Invoice"}
_HREF = {"mr": "/compass/purchasing/mr/", "po": "/compass/purchasing/po/", "pr": "/compass/purchasing/pr/", "pi": "/compass/purchasing/pi/"}


@frappe.whitelist()
def purchasing_search(q="", kind="all", limit=40):
    """One search across Material Requests, Purchase Orders, Receiving and Purchase Invoices (CR-002).

    Matches document numbers, supplier, supplier invoice / confirmation / file / delivery note numbers, BL,
    and each line's item code, name and description; containers and BLs through the invoices and shipments
    (landing.container_search). `kind` narrows to mr / po / pr / pi. Ranked by rules.doc_search: exact
    references first, near spellings last. Each hit names its type.
    """
    _assert_purchasing_role()
    from frappe.utils import strip_html
    q = (q or "").strip()
    if len(q) < 2:
        return []
    kinds = [kind] if kind in _DOC_SEARCH else list(_DOC_SEARCH)
    docs = []
    for k in kinds:
        dt, child, fields, refs, sup, date_f, filters = _DOC_SEARCH[k]
        meta_ok = [f for f in fields if f == "name" or frappe.get_meta(dt).has_field(f)]
        rows = frappe.get_all(dt, filters=filters, fields=meta_ok + ["creation", "docstatus"], order_by="creation desc",
                              limit_page_length=_SEARCH_WINDOW)
        names = [r.name for r in rows]
        lines = frappe.get_all(child, filters={"parent": ["in", names]}, fields=["parent", "item_code", "item_name", "description"],
                               parent_doctype=dt, limit_page_length=0) if names else []
        by = {}
        for l in lines:
            by.setdefault(l.parent, []).append({"code": l.item_code, "text": " ".join(filter(None, [l.item_name, strip_html(l.description or "")]))})
        for r in rows:
            docs.append({"kind": k, "doctype": dt, "name": r.name, "created": str(r.creation),
                         "refs": [r.get(f) for f in refs if f in meta_ok],
                         "fields": {"supplier": r.get(sup) if sup else None, "raised by": (r.get("owner") or "").split("@")[0] if k == "mr" else None},
                         "items": by.get(r.name, []),
                         "supplier": r.get(sup) if sup else None, "status": "Draft" if r.docstatus == 0 else r.get("status"),
                         "date": str(r.get(date_f)) if r.get(date_f) else None})
    hits = rules.doc_search(q, docs, limit=int(limit or 40))
    out = [{"kind": h["kind"], "type": _LABEL[h["kind"]], "name": h["name"], "supplier": h.get("supplier"), "date": h.get("date"),
            "status": h.get("status"), "created": h.get("created"), "match": h["match"], "score": h["score"],
            "href": _HREF[h["kind"]] + h["name"]} for h in hits]
    if kind in ("all", "pi") and len(landing_rules.norm_container(q)) >= landing_rules.MIN_CONTAINER_CHARS:
        from vcl_procurement.api import landing
        seen = {o["name"] for o in out}
        for c in landing.container_search(q):
            n = c.get("invoice")
            if n and n not in seen:
                seen.add(n)
                out.append({"kind": "pi", "type": "Purchase Invoice", "name": n, "supplier": c.get("supplier"), "date": None,
                            "status": "Draft" if c.get("draft") else None, "created": None, "score": 95,
                            "match": "container " + c["container"] if c.get("kind") == "container" else "bill of lading",
                            "href": _HREF["pi"] + n})
            elif not n and c.get("shipment") and kind == "all":
                out.append({"kind": "shipment", "type": "Import Shipment", "name": c["shipment"], "supplier": c.get("supplier"), "date": None,
                            "status": c.get("stage"), "created": None, "score": 95, "match": "bill of lading", "href": "/compass/imports"})
        out.sort(key=lambda r: -r["score"])
    return out[: int(limit or 40)]
