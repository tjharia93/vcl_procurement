"""Purchase orders, for every front end that needs them.

Moved out of `vcl_compass/api/purchasing.py` on 4 September 2026. It was 219
lines of purchase logic living inside the app that is only supposed to hold
screens, which broke the rule that Compass stays rebrandable or disposable
without touching a record — and left the Android app with nowhere legitimate to
call, since it would have had to reach into the browser front end for its data.

Behaviour is unchanged for the Compass screens that already use this. Two things
did have to change, both because a backend app must not depend on the UI app:

  the gate    was `vcl_compass.scope._resolve_scope()["is_restricted"]`. Now the
              native ERPNext purchasing roles. Deliberately NOT a plain
              `has_permission("Purchase Order", "read")` — Stock User carries
              read on Purchase Order and includes sales reps, so that would have
              quietly widened access rather than preserved it.

  overview()  stayed in vcl_compass. It joins this data with the imports board
              to compose one landing screen, which is presentation, not domain.
              It calls open_orders() from here.

**The price rule is enforced at this boundary.** A Purchase Order Item carries
seven fields that feed the price, and the interaction between them is what sent
Labchem an order at 280 against a quote of 250. `get_purchase_order` returns
`rate` and `amount` and nothing else — price_list_rate, margin_type,
margin_rate_or_amount, rate_with_margin and both discount fields never leave the
server, so no screen can show them and no later write path can round-trip one
back by accident.
"""

import json

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, today

OPEN_STATUSES = ["To Receive and Bill", "To Receive", "To Bill"]

# Purchasing is not rep work. These are ERPNext's own roles and they are already
# assigned — Purchase Manager to four accounts, Purchase User to three.
PURCHASING_ROLES = {"Purchase Manager", "Purchase User", "System Manager"}


def _assert_purchasing_role():
    if not (PURCHASING_ROLES & set(frappe.get_roles())):
        frappe.throw(_("Purchasing is not available to your role."),
                     frappe.PermissionError)


# --- shaping, shared by every client ----------------------------------------

def _iso(value):
    return getdate(value).isoformat() if value else None


def _shape_order(row):
    return {
        "name": row.get("name"),
        "supplier": row.get("supplier"),
        "supplier_name": row.get("supplier_name") or row.get("supplier"),
        "date": _iso(row.get("transaction_date")),
        "due": _iso(row.get("schedule_date")),
        "status": row.get("status"),
        "workflow_state": row.get("workflow_state"),
        "currency": row.get("currency"),
        "total": flt(row.get("grand_total")),
        "is_draft": row.get("docstatus") == 0,
        "is_cancelled": row.get("docstatus") == 2,
        "fully_received": flt(row.get("per_received")) >= 100,
        "fully_billed": flt(row.get("per_billed")) >= 100,
    }


def _shape_line(row):
    """One order line, with every price field except `rate` withheld."""
    return {
        "idx": row.get("idx"),
        "item_code": row.get("item_code"),
        "item_name": row.get("item_name") or row.get("item_code"),
        "qty": flt(row.get("qty")),
        "uom": row.get("uom"),
        "rate": flt(row.get("rate")),
        "amount": flt(row.get("amount")),
        "due": _iso(row.get("schedule_date")),
        "warehouse": row.get("warehouse"),
        "material_request": row.get("material_request"),
        "received_qty": flt(row.get("received_qty")),
    }


# --- the list the Compass screen already uses, unchanged in shape -----------

@frappe.whitelist()
def open_orders(status=None, supplier=None):
    _assert_purchasing_role()
    now = today()

    filters = {"docstatus": 1, "status": ["in", [status] if status else OPEN_STATUSES]}
    if supplier:
        filters["supplier"] = ["like", f"%{supplier}%"]

    rows = frappe.get_all(
        "Purchase Order", filters=filters,
        fields=["name", "supplier", "transaction_date", "schedule_date", "status",
                "base_grand_total", "per_received", "per_billed", "currency"],
        order_by="transaction_date asc", limit_page_length=500)

    out = []
    for r in rows:
        recv, bill = flt(r.per_received), flt(r.per_billed)
        due = str(r.schedule_date) if r.schedule_date else None
        # Value still to come in: the portion of the order not yet received.
        awaiting = flt(r.base_grand_total) * max(0.0, 100.0 - recv) / 100.0
        out.append({
            "name": r.name,
            "supplier": r.supplier,
            "date": str(r.transaction_date) if r.transaction_date else None,
            "age_days": date_diff(now, getdate(r.transaction_date)) if r.transaction_date else None,
            "due": due,
            "overdue": bool(due and due < now and recv < 100),
            "status": r.status,
            "value": flt(r.base_grand_total),
            "currency": r.currency,
            "per_received": recv,
            "per_billed": bill,
            # Billed but never received is the anomaly worth surfacing: the
            # supplier has invoiced, ERPNext has no record of the goods.
            "billed_not_received": bool(bill > 0 and recv == 0),
            "awaiting_value": awaiting,
            "desk_url": f"/app/purchase-order/{r.name}",
        })

    by_status = {}
    for r in out:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    return {
        "rows": out,
        "total": len(out),
        "total_value": sum(r["value"] for r in out),
        "awaiting_value": sum(r["awaiting_value"] for r in out),
        "overdue": sum(1 for r in out if r["overdue"]),
        "nothing_received": sum(1 for r in out if r["per_received"] == 0),
        "billed_not_received": sum(1 for r in out if r["billed_not_received"]),
        "by_status": by_status,
        "statuses": OPEN_STATUSES,
        "currency": frappe.db.get_default("currency") or "KES",
        "today": now,
    }


# --- new: one order, for a phone screen -------------------------------------

@frappe.whitelist()
def get_purchase_order(name):
    """One order with its lines. The detail view the Compass screen never had."""
    _assert_purchasing_role()
    if not name:
        frappe.throw(_("A purchase order name is required"))

    doc = frappe.get_doc("Purchase Order", name)
    doc.check_permission("read")

    out = _shape_order(doc.as_dict())
    out["lines"] = [_shape_line(i.as_dict()) for i in doc.get("items") or []
                    if flt(i.rate) or flt(i.qty)]
    out["net_total"] = flt(doc.net_total)
    out["tax_total"] = flt(doc.total_taxes_and_charges)
    out["material_requests"] = sorted({
        i.material_request for i in doc.get("items") or [] if i.material_request
    })
    out["desk_url"] = f"/app/purchase-order/{doc.name}"
    return out


# --- receipts, moved verbatim ------------------------------------------------

@frappe.whitelist()
def receipt_preview(po):
    """What is still to receive on a PO, line by line.

    Read the PO — do not recompute. `received_qty` on the item is what ERPNext
    and the warehouse already agree on; deriving it from receipts would drift.
    """
    _assert_purchasing_role()
    doc = frappe.get_doc("Purchase Order", po)
    doc.check_permission("read")
    if doc.docstatus != 1:
        frappe.throw(f"{po} is not submitted, so nothing can be received against it.")

    items = []
    for it in doc.items:
        pending = flt(it.qty) - flt(it.received_qty)
        items.append({
            "idx": it.idx,
            "item_code": it.item_code,
            "item_name": it.item_name,
            "uom": it.uom,
            "ordered": flt(it.qty),
            "received": flt(it.received_qty),
            "pending": pending if pending > 0 else 0,
            "rate": flt(it.rate),
        })
    return {
        "po": doc.name,
        "supplier": doc.supplier,
        "currency": doc.currency,
        "status": doc.status,
        "per_received": flt(doc.per_received),
        "items": items,
        "anything_pending": any(i["pending"] > 0 for i in items),
    }


@frappe.whitelist()
def create_receipt(po, lines=None):
    """Create a DRAFT Purchase Receipt against a PO.

    Draft only — submission stays a human act in the Desk, same standing rule
    as everywhere else. `lines` is an optional {idx: qty} map for a partial
    receipt; omitted means receive everything still pending.
    """
    _assert_purchasing_role()
    if isinstance(lines, str):
        lines = json.loads(lines or "null")

    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

    pr = make_purchase_receipt(po)
    if lines:
        wanted = {int(k): flt(v) for k, v in lines.items()}
        keep = []
        for row in pr.items:
            qty = wanted.get(int(row.idx))
            if qty is None or qty <= 0:
                continue
            row.qty = qty
            row.received_qty = qty
            keep.append(row)
        if not keep:
            frappe.throw("No quantities to receive.")
        pr.items = keep

    if not pr.items:
        frappe.throw(f"Nothing left to receive on {po}.")

    pr.insert()
    return {
        "name": pr.name,
        "docstatus": pr.docstatus,
        "po": po,
        "supplier": pr.supplier,
        "posting_date": str(pr.posting_date),
        "items": [{"item_code": i.item_code, "qty": flt(i.qty), "uom": i.uom}
                  for i in pr.items],
        "desk_url": f"/app/purchase-receipt/{pr.name}",
    }
