"""One purchase order: read it, edit a draft, choose its taxes, push it through the workflow.

This is the server side of what the /purchasing-dev prototype did in the
browser. Every rule lives in `rules.py`; this file only reads and writes
ERPNext documents.

**The price rule still holds.** A line leaves this API as `rate` and `amount`.
The seven ERPNext price fields (price_list_rate, margin_type, margin_rate_or_amount,
rate_with_margin, both discounts...) never leave the server, and a save takes
`rate` alone - `po_price_guard` then makes it authoritative on validate.

**Only a draft can be edited**, and never its supplier, currency or order type:
changing the order type would mean changing the tax template, and that is a new
order, not an edit.

Taxes are typed in KES. ERPNext stores the document-currency figure, so an
Actual row's amount is `kes / conversion_rate` (see rules.kes_to_doc).
"""

import frappe
from frappe import _
from frappe.utils import flt

from vcl_procurement.api import rules
from vcl_procurement.api.purchasing import _assert_purchasing_role

PO = "Purchase Order"

# What a tax row may carry over the wire. Anything else is dropped rather than
# trusted: account_head and cost_center come from the template rows.
_TAX_FIELDS = ("description", "account_head", "charge_type", "rate", "row_id",
               "add_deduct_tax", "category", "cost_center")

TAX_TEMPLATE_DEFAULTS = {"Local": "Kenya Tax - VCL", "Import": "Importation - VCL"}


def _iso(v):
    return rules.iso(v)


def _tax_row(t, rate, currency):
    return {
        "idx": t.idx, "name": t.name,
        "description": t.description, "account_head": t.account_head,
        "charge_type": t.charge_type, "rate": flt(t.rate), "row_id": t.row_id,
        "add_deduct_tax": t.add_deduct_tax or "Add", "category": t.category,
        "cost_center": t.cost_center,
        "tax_amount": flt(t.tax_amount),
        # Shown and typed in KES.
        "kes": flt(t.base_tax_amount) if t.base_tax_amount is not None
        else rules.doc_to_kes(t.tax_amount, rate, currency),
    }


def _connections(po_name):
    """Every receipt and invoice raised against this PO, drafts included."""
    out = {"receipts": [], "invoices": []}
    for child, parent, key in (("Purchase Receipt Item", "Purchase Receipt", "receipts"),
                               ("Purchase Invoice Item", "Purchase Invoice", "invoices")):
        rows = frappe.get_all(child, filters={"purchase_order": po_name, "docstatus": ["<", 2]},
                              fields=["parent", "docstatus"], parent_doctype=parent,
                              limit_page_length=500)
        seen = {}
        for r in rows:
            seen[r.parent] = {"name": r.parent, "draft": r.docstatus == 0}
        out[key] = list(seen.values())
    return out


def _workflow_actions(doc):
    """The actions THIS user may take now. Asked of the workflow, never assumed."""
    from frappe.model.workflow import get_transitions
    try:
        seen, out = set(), []
        for t in get_transitions(doc) or []:
            if t.action in seen:
                continue
            seen.add(t.action)
            out.append({"action": t.action, "next_state": t.next_state})
        return out
    except Exception:
        return []


def _payment_terms():
    return frappe.get_all("Payment Terms Template", pluck="name", order_by="name asc",
                          limit_page_length=200)


def _tax_templates():
    return frappe.get_all("Purchase Taxes and Charges Template", filters={"disabled": 0},
                          pluck="name", order_by="name asc", limit_page_length=200)


@frappe.whitelist()
def po_page(name):
    """Everything the purchase order screen needs, in one call."""
    _assert_purchasing_role()
    doc = frappe.get_doc(PO, name)
    doc.check_permission("read")
    rate = flt(doc.conversion_rate) or 1
    cur = doc.currency

    stock_uoms = {i.item_code: i.stock_uom for i in doc.items}
    lines = [{
        "name": i.name, "idx": i.idx, "item_code": i.item_code,
        "item_name": i.item_name or i.item_code, "qty": flt(i.qty), "uom": i.uom,
        "stock_uom": i.stock_uom or stock_uoms.get(i.item_code),
        "conversion_factor": flt(i.conversion_factor) or 1,
        "rate": flt(i.rate), "amount": flt(i.amount),
        "warehouse": i.warehouse, "due": _iso(i.schedule_date),
        "received_qty": flt(i.received_qty), "billed_amt": flt(i.billed_amt),
        "material_request": i.material_request,
    } for i in doc.items]

    taxes = [_tax_row(t, rate, cur) for t in doc.get("taxes") or []]
    return {
        "name": doc.name,
        "docstatus": doc.docstatus,
        "status": doc.status,
        "workflow_state": doc.get("workflow_state"),
        "editable": doc.docstatus == 0,
        "supplier": doc.supplier,
        "supplier_name": doc.supplier_name or doc.supplier,
        "currency": cur,
        "conversion_rate": rate,
        "order_type": doc.get("custom_order_type"),
        "date": _iso(doc.transaction_date),
        "required_by": _iso(doc.schedule_date),
        "order_confirmation_no": doc.get("order_confirmation_no"),
        "file_no": doc.get("custom_file_number"),
        "payment_terms_template": doc.get("payment_terms_template"),
        "taxes_and_charges": doc.get("taxes_and_charges"),
        "tax_category": doc.get("tax_category"),
        "net_total": flt(doc.net_total),
        "total_tax_kes": rules.signed_sum(taxes, "kes"),
        "grand_total": flt(doc.grand_total),
        "base_grand_total": flt(doc.base_grand_total),
        "per_received": flt(doc.per_received),
        "per_billed": flt(doc.per_billed),
        "lines": lines,
        "taxes": taxes,
        "schedule": [{
            "payment_term": p.payment_term, "description": p.description,
            "due": _iso(p.due_date), "portion": flt(p.invoice_portion),
            "amount": flt(p.payment_amount),
        } for p in doc.get("payment_schedule") or []],
        "connections": _connections(doc.name),
        "actions": _workflow_actions(doc) if doc.docstatus == 0 else [],
        "payment_terms_options": _payment_terms() if doc.docstatus == 0 else [],
        "tax_template_options": _tax_templates() if doc.docstatus == 0 else [],
        "desk_url": f"/app/purchase-order/{doc.name}",
    }


@frappe.whitelist()
def tax_template_rows(template):
    """The rows of one tax template, ready to become a PO's tax rows."""
    _assert_purchasing_role()
    from erpnext.controllers.accounts_controller import get_taxes_and_charges
    rows = get_taxes_and_charges("Purchase Taxes and Charges Template", template) or []
    return [{**{k: r.get(k) for k in _TAX_FIELDS}, "add_deduct_tax": r.get("add_deduct_tax") or "Add",
             "kes": 0.0} for r in rows]


def clean_tax_rows(rows, rate, currency):
    """Client tax rows -> what ERPNext should hold. KES on an Actual row becomes
    document currency; every other type keeps its rate and is recalculated by ERPNext."""
    rules.check_tax_rows(rows)
    out = []
    for i, r in enumerate(rows, start=1):
        row = {k: r.get(k) for k in _TAX_FIELDS}
        row["idx"] = i
        row["add_deduct_tax"] = row.get("add_deduct_tax") or "Add"
        if r.get("name"):
            row["name"] = r["name"]
        if row["charge_type"] == "Actual":
            row["tax_amount"] = rules.kes_to_doc(r.get("kes"), rate, currency)
            row["base_tax_amount"] = flt(r.get("kes"))
            row["row_id"] = None
        elif row["charge_type"] not in rules.PREVIOUS_ROW_TYPES:
            row["row_id"] = None
        out.append(row)
    return out


@frappe.whitelist()
def po_save(name, payload):
    """Save an edit to a DRAFT purchase order and return the fresh page.

    payload: {required_by, order_confirmation_no, file_no, payment_terms_template,
              taxes_and_charges, lines: [{name?, item_code, qty, uom, conversion_factor,
              rate, warehouse?}], taxes: [{...tax row, kes}]}
    Every key is optional; what is missing is left as it is.
    """
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    doc = frappe.get_doc(PO, name)
    doc.check_permission("write")
    if doc.docstatus != 0:
        frappe.throw(_("{0} is not a draft, so it cannot be edited.").format(name))

    data = {}
    if "required_by" in payload:
        if not payload["required_by"]:
            frappe.throw(_("A purchase order needs a required-by date."))
        data["schedule_date"] = payload["required_by"]
    for src, dst in (("order_confirmation_no", "order_confirmation_no"), ("file_no", "custom_file_number"),
                     ("payment_terms_template", "payment_terms_template"),
                     ("taxes_and_charges", "taxes_and_charges")):
        if src in payload:
            data[dst] = payload[src] or None

    if "lines" in payload:
        lines = payload["lines"] or []
        if not lines:
            frappe.throw(_("A purchase order needs at least one item."))
        stock = {ln["item_code"]: frappe.db.get_value("Item", ln["item_code"], "stock_uom")
                 for ln in lines if ln.get("item_code")}
        for ln in lines:
            if not ln.get("item_code") or stock.get(ln["item_code"]) is None:
                frappe.throw(_("Every line needs an item that exists."))
            if flt(ln.get("qty")) <= 0:
                frappe.throw(_("Every line needs a quantity."))
        try:
            rules.check_line_units(lines, stock)
        except rules.RuleError as e:
            frappe.throw(str(e))

        sd = data.get("schedule_date") or doc.schedule_date
        existing = {i.name: i for i in doc.items}
        rows = []
        for ln in lines:
            row = existing[ln["name"]].as_dict() if ln.get("name") in existing else {}
            row.update({"item_code": ln["item_code"], "qty": flt(ln["qty"]),
                        "uom": ln.get("uom") or stock[ln["item_code"]],
                        "conversion_factor": flt(ln.get("conversion_factor")) or 1.0,
                        # The only price field. po_price_guard clears anything that could re-derive it.
                        "rate": flt(ln.get("rate")), "schedule_date": sd})
            if ln.get("warehouse"):
                row["warehouse"] = ln["warehouse"]
            rows.append(row)
        data["items"] = rows

    if "taxes" in payload:
        try:
            data["taxes"] = clean_tax_rows(payload["taxes"] or [], flt(doc.conversion_rate) or 1, doc.currency)
        except rules.RuleError as e:
            frappe.throw(str(e))
        if any(r.get("account_head") is None for r in data["taxes"]):
            frappe.throw(_("Every tax row needs a tax account."))

    doc.update(data)
    doc.save()
    return po_page(name)


@frappe.whitelist()
def po_act(name, action):
    """Take a workflow action on a purchase order (Approve, Submit for Approval...).

    Only an action the workflow offers THIS user now is accepted. Returns the
    fresh page, whose `workflow_state` says what actually happened.
    """
    _assert_purchasing_role()
    from frappe.model.workflow import apply_workflow
    doc = frappe.get_doc(PO, name)
    doc.check_permission("read")
    allowed = {a["action"] for a in _workflow_actions(doc)}
    if action not in allowed:
        frappe.throw(_("The action {0} is not available on {1} right now.").format(action, name))
    apply_workflow(doc, action)
    return po_page(name)
