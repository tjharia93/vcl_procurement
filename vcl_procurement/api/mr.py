"""Material requests for the phone: raise, see, submit, and follow one through
to its order and its receipt.

The assistant raises a DRAFT request from words; the floor can also type one
here. Submission used to stay in the Desk; a purchasing role may now submit
from the phone. Each request answers with one stage word (mr_rules.stage) so
the screen never re-derives it.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from vcl_procurement.api import mr_rules as R
from vcl_procurement.api.purchasing import _assert_purchasing_role

HEAD_FIELDS = ["name", "docstatus", "transaction_date", "schedule_date", "status",
               "per_ordered", "per_received", "owner", "title"]


def _row(r, items=None):
    word = R.stage(r)
    return {
        "name": r["name"], "stage": word, "step": R.step_index(word), "is_open": R.is_open(word),
        "date": str(r.get("transaction_date") or "")[:10] or None,
        "needed_by": str(r.get("schedule_date") or "")[:10] or None,
        "status": r.get("status"), "docstatus": int(r.get("docstatus") or 0),
        "per_ordered": flt(r.get("per_ordered")), "per_received": flt(r.get("per_received")),
        "raised_by": (r.get("owner") or "").split("@")[0],
        "title": r.get("title") or None,
        "items": items if items is not None else [],
    }


def _items(names):
    if not names:
        return {}
    out = {}
    for it in frappe.get_all("Material Request Item", filters={"parent": ["in", names]},
                             fields=["parent", "name", "item_code", "item_name", "qty", "uom",
                                     "ordered_qty", "received_qty", "warehouse", "schedule_date"],
                             parent_doctype="Material Request", order_by="idx asc", limit_page_length=0):
        out.setdefault(it.parent, []).append({
            "name": it.name, "item_code": it.item_code, "item_name": it.item_name or it.item_code,
            "qty": flt(it.qty), "uom": it.uom, "ordered": flt(it.ordered_qty), "received": flt(it.received_qty),
            "warehouse": it.warehouse, "needed_by": str(it.schedule_date or "")[:10] or None,
        })
    return out


@frappe.whitelist()
def my_requests(scope="open", limit=60):
    """The requests the phone shows: drafts (anyone's, this is a shared queue),
    then submitted ones still open; `scope=done` lists the received and stopped."""
    _assert_purchasing_role()
    filters = {"material_request_type": "Purchase"}
    rows = frappe.get_all("Material Request", filters=dict(filters, docstatus=["<", 2]), fields=HEAD_FIELDS,
                          order_by="docstatus asc, transaction_date desc, creation desc",
                          limit_page_length=int(limit) * 3)
    rows = [dict(r) for r in rows]
    want_open = scope != "done"
    rows = [r for r in rows if R.is_open(R.stage(r)) == want_open][: int(limit)]
    items = _items([r["name"] for r in rows])
    out = [_row(r, items.get(r["name"], [])) for r in rows]
    return {"scope": "open" if want_open else "done", "count": len(out), "rows": out,
            "drafts": sum(1 for r in out if r["stage"] == "Draft"),
            "to_order": sum(1 for r in out if r["stage"] in ("To order", "Partly ordered")),
            "to_receive": sum(1 for r in out if r["stage"] in ("Ordered", "Partly received"))}


@frappe.whitelist()
def request(name):
    """One request with its lines, and the purchase orders raised from it with
    how much of each is received, so the phone can offer Receive from here."""
    _assert_purchasing_role()
    doc = frappe.get_doc("Material Request", name)
    doc.check_permission("read")
    head = {k: doc.get(k) for k in HEAD_FIELDS}
    row = _row(head, _items([name]).get(name, []))
    pos = {}
    for pi in frappe.get_all("Purchase Order Item", filters={"material_request": name},
                             fields=["parent", "item_code", "qty", "received_qty"],
                             parent_doctype="Purchase Order", limit_page_length=0):
        pos.setdefault(pi.parent, []).append(pi)
    orders = []
    if pos:
        for po in frappe.get_all("Purchase Order", filters={"name": ["in", list(pos)], "docstatus": ["<", 2]},
                                 fields=["name", "docstatus", "supplier", "supplier_name", "status",
                                         "per_received", "per_billed", "transaction_date", "schedule_date"],
                                 limit_page_length=0):
            orders.append({
                "name": po.name, "docstatus": int(po.docstatus), "supplier": po.supplier_name or po.supplier,
                "status": po.status, "per_received": flt(po.per_received), "per_billed": flt(po.per_billed),
                "date": str(po.transaction_date or "")[:10] or None,
                "expected": str(po.schedule_date or "")[:10] or None,
                "can_receive": int(po.docstatus) == 1 and flt(po.per_received) < 100
                and po.status not in ("Closed", "Completed"),
                "lines": [{"item_code": l.item_code, "qty": flt(l.qty), "received": flt(l.received_qty)}
                          for l in pos[po.name]],
            })
    row["orders"] = orders
    row["can_submit"] = row["stage"] == "Draft" and bool(doc.has_permission("submit"))
    return row


@frappe.whitelist(methods=["POST"])
def create_request(lines, schedule_date=None, title=None):
    """A typed request, raised as a draft the way the assistant raises one."""
    _assert_purchasing_role()
    if isinstance(lines, str):
        lines = json.loads(lines or "[]")
    clean, problem = R.clean_lines(lines)
    if problem:
        frappe.throw(problem)
    needed = getdate(schedule_date) if schedule_date else getdate(today())
    company = (frappe.defaults.get_user_default("Company")
               or frappe.db.get_single_value("Global Defaults", "default_company"))
    fallback = frappe.db.get_single_value("Stock Settings", "default_warehouse")
    doc = frappe.new_doc("Material Request")
    doc.material_request_type = "Purchase"
    doc.company = company
    doc.schedule_date = needed
    if title:
        doc.title = str(title)[:140]
    for ln in clean:
        warehouse = (ln["warehouse"]
                     or frappe.db.get_value("Item Default", {"parent": ln["item_code"], "company": company},
                                            "default_warehouse")
                     or fallback)
        if not warehouse:
            frappe.throw(_("Which warehouse is {0} for? It has no default.").format(ln["item_code"]))
        line = {"item_code": ln["item_code"], "qty": ln["qty"], "schedule_date": needed, "warehouse": warehouse}
        if ln["uom"]:
            line["uom"] = ln["uom"]
        doc.append("items", line)
    doc.insert()
    frappe.db.commit()
    return request(doc.name)


@frappe.whitelist(methods=["POST"])
def submit_request(name):
    """Submit a draft so purchasing can order against it. Permission is ERPNext's."""
    _assert_purchasing_role()
    doc = frappe.get_doc("Material Request", name)
    if doc.docstatus != 0:
        frappe.throw(_("{0} is not a draft.").format(name))
    doc.check_permission("submit")
    doc.submit()
    frappe.db.commit()
    return request(name)
