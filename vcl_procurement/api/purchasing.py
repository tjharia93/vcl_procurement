"""One purchasing API, two front ends.

Compass in the browser and the Android app both need the same purchase orders.
Building the browser first and retrofitting the phone is how you end up with two
subtly different truths, so the data is shaped here, once, and both clients
render what they are given.

Phase 1 is read-only: see the orders, open one, read the lines.

**The price rule is enforced at this boundary.** A Purchase Order Item carries
seven fields that feed the price, and the interaction between them is what put
an order to Labchem at 280 against a quote of 250 on 4 Sep 2026. This API
returns `rate` and `amount` and nothing else. `price_list_rate`, `margin_type`,
`margin_rate_or_amount`, `rate_with_margin` and both discount fields are never
sent to a client, so no screen can display them, no user can be confused by
them, and no future write path can accidentally round-trip one back.

Permissions are Frappe's. Nothing here uses ignore_permissions.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

LIST_FIELDS = [
    "name",
    "supplier",
    "supplier_name",
    "transaction_date",
    "schedule_date",
    "status",
    "workflow_state",
    "currency",
    "grand_total",
    "per_received",
    "per_billed",
    "docstatus",
]

# What a client is allowed to know about a line. Note what is absent.
LINE_FIELDS = [
    "idx",
    "item_code",
    "item_name",
    "description",
    "qty",
    "uom",
    "rate",
    "amount",
    "schedule_date",
    "warehouse",
    "material_request",
    "received_qty",
]

OPEN_STATUSES = ("Draft", "To Receive and Bill", "To Receive", "To Bill")


def _iso(value):
    return getdate(value).isoformat() if value else None


def _shape_order(row):
    """The list row. Same shape on a phone and in a browser."""
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


@frappe.whitelist()
def get_purchase_orders(open_only=1, supplier=None, limit=50, start=0):
    """Purchase orders the signed-in user is allowed to see, newest first."""
    filters = {}
    if int(open_only or 0):
        filters["status"] = ["in", OPEN_STATUSES]
    if supplier:
        filters["supplier"] = supplier

    rows = frappe.get_all(
        "Purchase Order",
        filters=filters,
        fields=LIST_FIELDS,
        order_by="transaction_date desc, creation desc",
        limit_page_length=int(limit),
        limit_start=int(start),
    )
    return [_shape_order(r) for r in rows]


@frappe.whitelist()
def get_purchase_order(name):
    """One order, with its lines. Raises if the user may not read it."""
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
    return out
