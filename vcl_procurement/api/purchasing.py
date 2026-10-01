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

from vcl_procurement.api import rules

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
                "base_grand_total", "grand_total", "per_received", "per_billed", "currency",
                "custom_order_type", "order_confirmation_no", "custom_file_number"],
        order_by="transaction_date asc", limit_page_length=500)
    links = _links_for([r.name for r in rows])
    ordered = _ordered_for([r.name for r in rows])

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
            # Overdue for so long that it is probably dead paper, not a late delivery.
            "stale": bool(due and due < now and recv < 100 and rules.is_stale(due, now)),
            "status": r.status,
            "value": flt(r.base_grand_total),
            "currency": r.currency,
            "total": flt(r.grand_total),            # in the order's own currency
            "order_type": r.custom_order_type or "Local",
            "confirmation_no": r.order_confirmation_no,
            "order_confirmation_no": r.order_confirmation_no,
            "file_no": r.custom_file_number,
            "ordered": ordered.get(r.name) or rules.ordered_summary([]),
            # Receipts and invoices already raised against it, drafts included.
            "connections": links.get(r.name, []),
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


def _links_for(po_names):
    """{po: [{kind, name, draft}]} for every receipt and invoice against these orders."""
    out = {}
    if not po_names:
        return out
    for child, parent, kind in (("Purchase Receipt Item", "Purchase Receipt", "Receipt"),
                                ("Purchase Invoice Item", "Purchase Invoice", "Invoice")):
        rows = frappe.get_all(child, filters={"purchase_order": ["in", po_names], "docstatus": ["<", 2]},
                              fields=["purchase_order", "parent", "docstatus"], parent_doctype=parent,
                              limit_page_length=5000)
        seen = set()
        for r in rows:
            key = (r.purchase_order, r.parent)
            if key in seen:
                continue
            seen.add(key)
            out.setdefault(r.purchase_order, []).append(
                {"kind": kind, "name": r.parent, "draft": r.docstatus == 0})
    return out


def _ordered_for(po_names):
    """{po: {text, more}}: what each order is for, from ONE query over all the orders' lines."""
    if not po_names:
        return {}
    rows = frappe.get_all("Purchase Order Item", filters={"parent": ["in", po_names]},
                          fields=["parent", "idx", "item_name", "description"],
                          order_by="parent, idx", parent_doctype="Purchase Order", limit_page_length=0)
    by_po = {}
    for r in rows:
        by_po.setdefault(r.parent, []).append(r)
    return {po: rules.ordered_summary(lines) for po, lines in by_po.items()}


@frappe.whitelist()
def draft_orders(mine=0):
    """Purchase orders still in draft (raised, not yet approved), newest first."""
    _assert_purchasing_role()
    filters = {"docstatus": 0}
    if int(mine):
        filters["owner"] = frappe.session.user
    rows = frappe.get_all(
        "Purchase Order", filters=filters,
        fields=["name", "supplier", "transaction_date", "schedule_date", "currency", "grand_total",
                "workflow_state", "custom_order_type", "owner", "order_confirmation_no",
                "custom_file_number"],
        order_by="modified desc", limit_page_length=200)
    ordered = _ordered_for([r.name for r in rows])
    return [{
        "name": r.name, "supplier": r.supplier, "date": _iso(r.transaction_date),
        "due": _iso(r.schedule_date), "currency": r.currency, "total": flt(r.grand_total),
        "workflow_state": r.workflow_state, "order_type": r.custom_order_type or "Local",
        "raised_by": r.owner,
        "confirmation_no": r.order_confirmation_no, "order_confirmation_no": r.order_confirmation_no,
        "file_no": r.custom_file_number,
        "stale": False,
        "ordered": ordered.get(r.name) or rules.ordered_summary([]),
    } for r in rows]


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

def _receipt_limits(doc):
    """Per PO row: (allowance %, max receivable). Item allowance if set, else Stock Settings'."""
    from vcl_procurement.api import rules
    glob = flt(frappe.db.get_single_value("Stock Settings", "over_delivery_receipt_allowance"))
    codes = list({it.item_code for it in doc.items})
    per_item = {r.name: flt(r.over_delivery_receipt_allowance) for r in frappe.get_all(
        "Item", filters={"name": ["in", codes]}, fields=["name", "over_delivery_receipt_allowance"], limit_page_length=0)} if codes else {}
    out = {}
    for it in doc.items:
        pending = max(0.0, flt(it.qty) - flt(it.received_qty))
        pct = rules.allowance_pct(per_item.get(it.item_code), glob)
        out[it.idx] = (pct, rules.max_receipt_qty(pending, pct))
    return glob, out


@frappe.whitelist()
def receipt_preview(po, pi=None):
    """What is still to receive on a PO, line by line - and the most each line may take.

    Read the PO - do not recompute. `received_qty` on the item is what ERPNext
    and the warehouse already agree on; deriving it from receipts would drift.
    The over-receipt allowance is ERPNext's own rule (the item's %, else Stock
    Settings'); `max_qty` is the ceiling the screen enforces and `create_receipt`
    checks again. Given `pi` (an invoice on this PO) each line also carries
    `invoice_qty`, what that invoice bills, matched on po_detail.
    """
    from vcl_procurement.api import rules
    _assert_purchasing_role()
    doc = frappe.get_doc("Purchase Order", po)
    doc.check_permission("read")
    if doc.docstatus != 1:
        frappe.throw(f"{po} is not submitted, so nothing can be received against it.")

    glob, limits = _receipt_limits(doc)
    inv_qty = None
    inv_meta = None
    if pi:
        inv = frappe.get_doc("Purchase Invoice", pi)
        inv.check_permission("read")
        inv_qty = rules.invoice_qty_by_line([{"name": r.name, "idx": r.idx} for r in doc.items],
                                            [{"po_detail": r.po_detail, "qty": r.qty} for r in inv.items])
        inv_meta = {"name": inv.name, "supplier_name": inv.supplier_name or inv.supplier, "bill_no": inv.bill_no,
                    "matched": bool(inv_qty)}

    items = []
    for it in doc.items:
        pending = flt(it.qty) - flt(it.received_qty)
        pct, mx = limits[it.idx]
        row = {
            "idx": it.idx,
            "item_code": it.item_code,
            "item_name": it.item_name,
            "uom": it.uom,
            "warehouse": it.warehouse,
            "ordered": flt(it.qty),
            "received": flt(it.received_qty),
            "pending": pending if pending > 0 else 0,
            "rate": flt(it.rate),
            "allowance_pct": pct,
            "max_qty": mx,
        }
        if inv_qty is not None:
            row["invoice_qty"] = inv_qty.get(it.idx, 0.0)
        items.append(row)
    return {
        "po": doc.name,
        "supplier": doc.supplier,
        "supplier_name": doc.supplier_name or doc.supplier,
        "currency": doc.currency,
        "status": doc.status,
        "grand_total": flt(doc.grand_total),
        "transaction_date": _iso(doc.transaction_date),
        "schedule_date": _iso(doc.schedule_date),
        "order_type": doc.get("custom_order_type"),
        "confirmation_no": doc.get("order_confirmation_no"),
        "per_received": flt(doc.per_received),
        "per_billed": flt(doc.per_billed),
        "global_allowance_pct": glob,
        "items": items,
        "invoice": inv_meta,
        "anything_pending": any(i["pending"] > 0 for i in items),
    }


@frappe.whitelist()
def create_receipt(po, lines=None, posting_date=None, supplier_delivery_note=None, submit=0):
    """Create a Purchase Receipt against a PO - a draft, or (submit=1) a submitted one.

    `lines` is an optional {idx: qty} map for a partial receipt; omitted means
    receive everything still pending. A quantity above what is pending is
    accepted up to the item's over-receipt allowance (checked here, then by
    ERPNext on save). `posting_date` is the day the goods arrived.

    With submit=1 the receipt is submitted and then RE-READ: `docstatus` in the
    answer is what the database says, never what we hoped. If the submit fails
    the draft is kept and `submit_error` says why.
    """
    from vcl_procurement.api import rules
    _assert_purchasing_role()
    if isinstance(lines, str):
        lines = json.loads(lines or "null")

    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

    po_doc = frappe.get_doc("Purchase Order", po)
    _glob, limits = _receipt_limits(po_doc)
    pr = make_purchase_receipt(po)
    if lines:
        wanted = {int(k): flt(v) for k, v in lines.items()}
        try:
            rules.check_receipt_lines(wanted, {idx: mx for idx, (_p, mx) in limits.items()})
        except rules.RuleError as e:
            frappe.throw(str(e))
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

    if posting_date:
        pr.set_posting_time = 1
        pr.posting_date = getdate(posting_date)
    if supplier_delivery_note:
        pr.supplier_delivery_note = str(supplier_delivery_note).strip()

    pr.insert()
    submit_error = None
    if int(submit or 0):
        frappe.db.savepoint("create_receipt_submit")
        try:
            pr.submit()
        except Exception as e:  # keep the draft; say why the submit failed
            frappe.db.rollback(save_point="create_receipt_submit")
            submit_error = getattr(e, "message", None) or str(e)
    docstatus = frappe.db.get_value("Purchase Receipt", pr.name, "docstatus")
    return {
        "name": pr.name,
        "docstatus": docstatus,
        "po": po,
        "supplier": pr.supplier,
        "posting_date": str(frappe.db.get_value("Purchase Receipt", pr.name, "posting_date")),
        "items": [{"item_code": i.item_code, "qty": flt(i.qty), "uom": i.uom}
                  for i in pr.items],
        "submit_error": submit_error,
        "desk_url": f"/app/purchase-receipt/{pr.name}",
    }
