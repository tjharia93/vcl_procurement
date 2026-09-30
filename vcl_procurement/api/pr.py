"""One purchase receipt (material receiving) - read it, submit it.

The server side of the Purchase Receipt screen prototyped at /purchasing-dev
(30 Sep 2026). Creating a receipt is `purchasing.create_receipt`; this file is
the page that shows one and the submit that books stock.

**Submit is confirmed by re-reading.** `pr_submit` submits, then reads
`docstatus` back from the database and returns that. A submit that throws
half-way is rolled back to a savepoint so it cannot leave a half-posted
receipt behind.
"""

import frappe
from frappe.utils import flt, getdate

from vcl_procurement.api.purchasing import _assert_purchasing_role

PR = "Purchase Receipt"


def _iso(v):
    return getdate(v).isoformat() if v else None


def _page(doc):
    return {
        "name": doc.name,
        "docstatus": doc.docstatus,
        "status": doc.status,
        "supplier": doc.supplier,
        "supplier_name": doc.supplier_name or doc.supplier,
        "posting_date": _iso(doc.posting_date),
        "supplier_delivery_note": doc.get("supplier_delivery_note"),
        "currency": doc.currency,
        "conversion_rate": flt(doc.conversion_rate) or 1,
        "per_billed": flt(doc.per_billed),
        "net_total": flt(doc.total),
        "base_net_total": flt(doc.base_total),
        "grand_total": flt(doc.grand_total),
        "base_grand_total": flt(doc.base_grand_total),
        "purchase_orders": sorted({r.purchase_order for r in doc.items if r.purchase_order}),
        "lines": [{
            "idx": r.idx,
            "item_code": r.item_code,
            "item_name": r.item_name,
            "qty": flt(r.qty),
            "uom": r.uom,
            "rate": flt(r.rate),
            "amount": flt(r.amount),
            "warehouse": r.warehouse,
            "purchase_order": r.purchase_order,
        } for r in doc.items],
        "taxes": [{
            "idx": t.idx,
            "description": t.description,
            "rate": flt(t.rate),
            "amount": flt(t.tax_amount),
            "base_amount": flt(t.base_tax_amount),
        } for t in doc.taxes],
        "can_submit": doc.docstatus == 0,
    }


@frappe.whitelist()
def pr_page(name):
    _assert_purchasing_role()
    doc = frappe.get_doc(PR, name)
    doc.check_permission("read")
    return _page(doc)


@frappe.whitelist()
def pr_submit(name):
    """Submit a draft receipt, then re-read it. Returns the page as the database now has it."""
    _assert_purchasing_role()
    doc = frappe.get_doc(PR, name)
    doc.check_permission("submit")
    if doc.docstatus != 0:
        frappe.throw(f"{name} is not a draft, so it cannot be submitted.")
    frappe.db.savepoint("pr_submit")
    try:
        doc.submit()
    except Exception:
        frappe.db.rollback(save_point="pr_submit")
        raise
    fresh = frappe.get_doc(PR, name)
    if fresh.docstatus != 1:
        frappe.throw(f"The server replied, but {name} is still a draft. Nothing has posted.")
    return _page(fresh)
