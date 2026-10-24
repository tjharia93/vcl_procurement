"""What the phone notifies about: purchasing events since it last asked.

The phone polls `feed(since)` from its background worker and posts a
notification per new event; ids are stable, so asking twice never notifies
twice. Events a person caused themselves are left out. Built for polling now;
a push service can later call the same rules.
"""

import frappe
from frappe.utils import add_days, get_datetime, now_datetime

from vcl_procurement.api import notify_rules as R
from vcl_procurement.api.purchasing import PURCHASING_ROLES


def _items(names):
    out = {}
    if not names:
        return out
    for it in frappe.get_all("Material Request Item", filters={"parent": ["in", names]},
                             fields=["parent", "item_code", "item_name", "qty", "uom"],
                             parent_doctype="Material Request", order_by="idx asc", limit_page_length=0):
        out.setdefault(it.parent, []).append(dict(it))
    return out


@frappe.whitelist()
def feed(since=None):
    """Events after `since` (ISO datetime; at most a week back), newest first.

    Anyone without a purchasing role gets an empty feed rather than an error,
    so the phone's worker can ask for every signed-in user without a 403.
    """
    user = frappe.session.user
    now = now_datetime()
    if not (PURCHASING_ROLES & set(frappe.get_roles(user))):
        return {"now": str(now)[:19], "events": []}
    floor = add_days(now, -7)
    try:
        start = max(get_datetime(since), floor) if since else add_days(now, -1)
    except Exception:
        start = add_days(now, -1)

    events = []
    raised = frappe.get_all(
        "Material Request",
        filters={"material_request_type": "Purchase", "docstatus": ["<", 2],
                 "creation": [">", start], "owner": ["!=", user]},
        fields=["name", "owner", "title", "schedule_date", "creation as at"], limit_page_length=50)
    submitted = frappe.get_all(
        "Material Request",
        filters={"material_request_type": "Purchase", "docstatus": 1,
                 "modified": [">", start], "per_ordered": ["<", 100], "modified_by": ["!=", user]},
        fields=["name", "owner", "title", "schedule_date", "modified as at"], limit_page_length=50)
    items = _items(list({r.name for r in raised} | {r.name for r in submitted}))
    events += [R.mr_event(dict(r), items.get(r.name), "mr_raised") for r in raised]
    events += [R.mr_event(dict(r), items.get(r.name), "mr_submitted") for r in submitted]

    po_fields = ["name", "owner", "supplier", "supplier_name", "modified as at"]
    if frappe.db.has_column("Purchase Order", "workflow_state"):
        to_approve = frappe.get_all(
            "Purchase Order",
            filters={"docstatus": 0, "modified": [">", start], "owner": ["!=", user],
                     "workflow_state": ["like", "%Pending%"]},
            fields=po_fields, limit_page_length=50)
        events += [R.po_event(dict(r), "po_to_approve") for r in to_approve]
    received = frappe.get_all(
        "Purchase Order",
        filters={"docstatus": 1, "modified": [">", start], "per_received": [">=", 100],
                 "per_billed": ["<", 100]},
        fields=po_fields, limit_page_length=50)
    events += [R.po_event(dict(r), "po_received") for r in received]

    return {"now": str(now)[:19], "events": R.newest_first(events)}
