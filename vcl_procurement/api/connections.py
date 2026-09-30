"""The Connections bar: every purchasing document linked to this one.

Purchase Order  -> its material requests, receipts, invoices.
Purchase Receipt-> its orders, the invoices raised on it or on the same orders, the other receipts.
Purchase Invoice-> its orders, the receipts (named on a line or raised on the same orders), the other invoices.
Material Request-> the purchase orders raised from it.

One call for any of the four, so a screen never has to know how they are wired. Read-only.
Ordering and de-duplication are pure functions (`assemble`), tested without a site.
"""

import frappe

from vcl_procurement.api.purchasing import _assert_purchasing_role

MR, PO, PR, PI = "Material Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice"
ORDER = (MR, PO, PR, PI)


def assemble(found):
    """{doctype: {name: {ds?, st?}}} -> the flat, ordered list the screen renders.

    Material request, order, receipt, invoice; names sorted inside each. `draft` is true only when
    the document's docstatus is known to be 0. `status` is ERPNext's status when it was read.
    """
    out = []
    for dt in ORDER:
        for name in sorted(found.get(dt, {})):
            c = found[dt][name]
            out.append({"doctype": dt, "name": name, "status": c.get("st"), "draft": c.get("ds") == 0})
    return out


def _child(child, parent, filters, fields=("parent", "docstatus")):
    return frappe.get_all(child, filters=filters, fields=list(fields), parent_doctype=parent, limit_page_length=1000)


def _uniq(values):
    return sorted({v for v in values if v})


@frappe.whitelist()
def connections(doctype, name):
    _assert_purchasing_role()
    if doctype not in ORDER:
        frappe.throw(f"{doctype} has no connections here.")
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    items = doc.get("items") or []
    found = {dt: {} for dt in ORDER}

    if doctype == MR:
        for r in _child("Purchase Order Item", PO, {"material_request": name, "docstatus": ["<", 2]}):
            found[PO][r.parent] = {"ds": r.docstatus}
        po_names = list(found[PO])
    elif doctype == PO:
        po_names = [name]
        for m in _uniq(i.get("material_request") for i in items):
            found[MR][m] = {}
    else:
        po_names = _uniq(i.get("purchase_order") for i in items)
        for n in po_names:
            found[PO][n] = {}

    if doctype != MR and po_names:
        for child, parent in (("Purchase Receipt Item", PR), ("Purchase Invoice Item", PI)):
            for r in _child(child, parent, {"purchase_order": ["in", po_names], "docstatus": ["<", 2]}):
                if not (parent == doctype and r.parent == name):
                    found[parent][r.parent] = {"ds": r.docstatus}
        if doctype == PR:
            for r in _child("Purchase Invoice Item", PI, {"purchase_receipt": name, "docstatus": ["<", 2]}):
                found[PI][r.parent] = {"ds": r.docstatus}
        if doctype == PI:
            for n in _uniq(i.get("purchase_receipt") for i in items):
                found[PR].setdefault(n, {})

    # Status and docstatus of the ones known only by name (and ERPNext's own status for the rest).
    for dt in (MR, PO, PR, PI):
        names = list(found[dt])
        if not names:
            continue
        for r in frappe.get_all(dt, filters={"name": ["in", names]}, fields=["name", "status", "docstatus"],
                                limit_page_length=1000):
            if r.name in found[dt]:
                found[dt][r.name].update({"st": r.status, "ds": r.docstatus})
    return assemble(found)
