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
            "name": r.name, "idx": r.idx,
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
        'has_reels': bool(doc.get('custom_reel_request_id')) or any(str(r.get('batch_no') or '').startswith('VCL-RL-') for r in doc.items),
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


@frappe.whitelist()
def pr_save(name, payload):
    """Edit a draft in one transaction; keep PO links and original receipt row identities."""
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    doc = frappe.get_doc(PR, name)
    doc.check_permission("write")
    if doc.docstatus != 0:
        frappe.throw("Only a draft receipt can be edited.")
    posting = payload.get("posting_date")
    if not posting:
        frappe.throw("Choose the receipt date.")
    wanted = {r.get("name"): r for r in payload.get("lines") or []}
    if set(wanted) != {r.name for r in doc.items}:
        frappe.throw("Receipt lines changed. Reload the receipt before saving.")
    reels = bool(doc.get("custom_reel_request_id")) or any(str(r.get("batch_no") or "").startswith("VCL-RL-") for r in doc.items)
    for row in doc.items:
        raw = wanted[row.name]
        qty, rate = flt(raw.get("qty")), flt(raw.get("rate"))
        if qty <= 0 or rate < 0:
            frappe.throw("Every line needs a positive quantity and a non-negative rate.")
        if reels and abs(qty - flt(row.qty)) > 1e-9:
            frappe.throw("Reel quantities come from the packing list. Recreate this draft through reel receiving to change them.")
        row.qty, row.rate = qty, rate
        row.update({"price_list_rate": rate, "margin_type": "", "margin_rate_or_amount": 0,
                    "discount_percentage": 0, "discount_amount": 0, "rate_with_margin": 0})
    doc.posting_date = str(getdate(posting))
    doc.set_posting_time = 1
    doc.supplier_delivery_note = (payload.get("supplier_delivery_note") or "").strip() or None
    doc.save()
    return _page(frappe.get_doc(PR, name))
