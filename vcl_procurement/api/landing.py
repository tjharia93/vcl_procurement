"""Read APIs behind the procurement landing page (Material requests, Receiving,
Invoices tabs, and the container search). Read-only: nothing here writes.

Same gate as the rest of purchasing: ERPNext's own purchasing roles.
"""

import frappe
from frappe.utils import flt

from vcl_procurement.api import landing_rules as R
from vcl_procurement.api.purchasing import _assert_purchasing_role


def _children(child_doctype, parent_doctype, names, fields):
    if not names:
        return []
    return frappe.get_all(child_doctype, filters={"parent": ["in", names]}, fields=fields,
                          parent_doctype=parent_doctype, limit_page_length=0)


@frappe.whitelist()
def mr_list():
    """Submitted purchase-type material requests, newest first, with their items."""
    _assert_purchasing_role()
    rows = frappe.get_all(
        "Material Request",
        filters={"docstatus": 1, "material_request_type": "Purchase"},
        fields=["name", "transaction_date", "schedule_date", "status", "per_ordered", "per_received", "owner"],
        order_by="transaction_date desc, creation desc", limit_page_length=R.LIMIT)
    items = R.group_items(_children("Material Request Item", "Material Request", [r.name for r in rows],
                                    ["parent", "item_name", "qty", "uom"]))
    return [{
        "name": r.name, "date": str(r.transaction_date) if r.transaction_date else None,
        "needed_by": str(r.schedule_date) if r.schedule_date else None,
        "status": r.status, "per_ordered": flt(r.per_ordered), "per_received": flt(r.per_received),
        "raised_by": (r.owner or "").split("@")[0], "is_open": R.is_open_mr(r),
        "items": items.get(r.name, []),
    } for r in rows]


@frappe.whitelist()
def pr_list():
    """Purchase receipts (drafts and submitted, not returns), newest first, with linked POs."""
    _assert_purchasing_role()
    rows = frappe.get_all(
        "Purchase Receipt", filters={"docstatus": ["<", 2], "is_return": 0},
        fields=["name", "supplier", "supplier_name", "posting_date", "status", "docstatus",
                "grand_total", "base_grand_total", "currency", "per_billed"],
        order_by="posting_date desc, creation desc", limit_page_length=R.LIMIT)
    pos = R.group_pos(_children("Purchase Receipt Item", "Purchase Receipt", [r.name for r in rows],
                                ["parent", "purchase_order"]))
    return [{
        "name": r.name, "supplier": r.supplier, "supplier_name": r.supplier_name or r.supplier,
        "date": str(r.posting_date) if r.posting_date else None, "status": r.status, "docstatus": r.docstatus,
        "total": flt(r.grand_total), "base_total": flt(r.base_grand_total), "currency": r.currency,
        "per_billed": flt(r.per_billed), "pos": pos.get(r.name, []),
    } for r in rows]


@frappe.whitelist()
def pi_list():
    """Purchase invoices (drafts and submitted, not returns), newest first, with linked POs and containers."""
    _assert_purchasing_role()
    rows = frappe.get_all(
        "Purchase Invoice", filters={"docstatus": ["<", 2], "is_return": 0},
        fields=["name", "supplier", "supplier_name", "bill_no", "bill_date", "posting_date", "due_date",
                "status", "docstatus", "grand_total", "base_grand_total", "outstanding_amount",
                "conversion_rate", "currency", "custom_purchase_invoice_type"],
        order_by="posting_date desc, creation desc", limit_page_length=R.LIMIT)
    names = [r.name for r in rows]
    pos = R.group_pos(_children("Purchase Invoice Item", "Purchase Invoice", names, ["parent", "purchase_order"]))
    conts = {}
    for c in _children("Container Shipping Details", "Purchase Invoice", names, ["parent", "container_reference"]):
        n = R.norm_container(c.get("container_reference"))
        if n:
            conts.setdefault(c.get("parent"), []).append(n)
    return [{
        "name": r.name, "supplier": r.supplier, "supplier_name": r.supplier_name or r.supplier,
        "bill_no": r.bill_no, "bill_date": str(r.bill_date) if r.bill_date else None,
        "posting_date": str(r.posting_date) if r.posting_date else None,
        "due_date": str(r.due_date) if r.due_date else None,
        "status": r.status, "docstatus": r.docstatus, "total": flt(r.grand_total),
        "base_total": flt(r.base_grand_total), "outstanding": flt(r.outstanding_amount),
        "conversion_rate": flt(r.conversion_rate) or 1, "currency": r.currency,
        "invoice_type": r.custom_purchase_invoice_type, "pos": pos.get(r.name, []),
        "containers": conts.get(r.name, []),
    } for r in rows]


@frappe.whitelist()
def container_search(q=""):
    """Invoices carrying a container whose number contains `q` (spaces, dashes and case ignored)."""
    _assert_purchasing_role()
    if len(R.norm_container(q)) < R.MIN_CONTAINER_CHARS:
        return []
    rows = frappe.get_all(
        "Container Shipping Details", filters={"parenttype": "Purchase Invoice"},
        fields=["parent", "container_reference", "container_size"],
        parent_doctype="Purchase Invoice", limit_page_length=0)
    hits = [r for r in rows if R.container_matches(r.get("container_reference"), q)][:30]
    if not hits:
        return []
    parents = sorted({h.parent for h in hits})
    inv = {r.name: r for r in frappe.get_all(
        "Purchase Invoice", filters={"name": ["in", parents]},
        fields=["name", "supplier", "supplier_name", "bill_no", "docstatus", "custom_bill_of_lading_number"],
        limit_page_length=0)}
    pos = R.group_pos(_children("Purchase Invoice Item", "Purchase Invoice", parents, ["parent", "purchase_order"]))
    out = []
    for h in hits:
        i = inv.get(h.parent)
        out.append({
            "container": str(h.container_reference or "").strip(), "size": h.container_size,
            "invoice": h.parent, "supplier": (i.supplier_name or i.supplier) if i else None,
            "bill_no": i.bill_no if i else None, "pos": pos.get(h.parent, []),
            "bl_no": i.custom_bill_of_lading_number if i else None,
            "draft": bool(i and i.docstatus == 0),
        })
    return out
