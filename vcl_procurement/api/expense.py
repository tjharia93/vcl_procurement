"""KES expense invoices without a PO, previewed and created by the same builder."""
import math
import frappe
from frappe.utils import flt, getdate
from vcl_procurement.api.purchasing import _assert_purchasing_role
from vcl_procurement.api import rules

def _company():
    return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")

@frappe.whitelist()
def options():
    _assert_purchasing_role()
    company = _company()
    return {"company": company, "accounts": frappe.get_list("Account", filters={"company": company,
        "is_group": 0, "disabled": 0, "root_type": "Expense"}, pluck="name", order_by="name", limit_page_length=1000)}


def _build(payload):
    problem = rules.expense_bill_error(payload)
    if problem:
        frappe.throw(problem)
    company = _company()
    from erpnext.accounts.party import get_party_details
    party = get_party_details(party=payload["supplier"], party_type="Supplier", company=company,
        doctype="Purchase Invoice", posting_date=payload["bill_date"], bill_date=payload["bill_date"])
    if party.get("currency") and party["currency"] != "KES":
        frappe.throw("This supplier bills in another currency. Raise the invoice from a Purchase Order.")
    doc = frappe.new_doc("Purchase Invoice")
    doc.company, doc.supplier, doc.currency = company, payload["supplier"], "KES"
    doc.posting_date = doc.bill_date = str(getdate(payload["bill_date"]))
    doc.set_posting_time, doc.update_stock = 1, 0
    doc.bill_no = payload["bill_no"].strip()
    doc.custom_purchase_invoice_type = payload.get("invoice_type") or "Local Purchase"
    doc.credit_to, doc.tax_category = party.get("credit_to"), party.get("tax_category")
    doc.payment_terms_template = party.get("payment_terms_template")
    if not doc.payment_terms_template:
        doc.due_date = doc.bill_date
    cc = frappe.get_cached_value("Company", company, "cost_center")
    for raw in payload["lines"]:
        account = frappe.get_doc("Account", raw["account"])
        account.check_permission("read")
        if account.company != company or account.is_group or account.disabled or account.root_type != "Expense":
            frappe.throw("Pick an enabled expense account for this company.")
        doc.append("items", {"item_name": raw["description"].strip()[:140],
            "description": rules.html_from_text(raw["description"].strip()), "qty": flt(raw["qty"]),
            "rate": flt(raw["rate"]), "uom": "Nos", "stock_uom": "Nos", "conversion_factor": 1,
            "expense_account": raw["account"], "cost_center": cc})
    doc.flags.dont_auto_add_taxes = True
    doc.set_missing_values()
    doc.set("taxes", [])
    doc.taxes_and_charges = None
    if payload.get("vat", True):
        from erpnext.controllers.accounts_controller import get_taxes_and_charges
        doc.taxes_and_charges = "Kenya Tax - VCL"
        taxes = get_taxes_and_charges("Purchase Taxes and Charges Template", doc.taxes_and_charges) or []
        if not taxes:
            frappe.throw("The Kenya VAT template has no rows.")
        if payload.get("manual_vat"):
            amount = payload.get("vat_amount")
            if len(taxes) != 1 or amount is None or not math.isfinite(flt(amount)) or flt(amount) < 0:
                frappe.throw("Enter a non-negative VAT amount; fixed VAT requires a single-row template.")
            taxes[0].update(charge_type="Actual", rate=0, row_id=None, tax_amount=flt(amount))
        for tax in taxes:
            doc.append("taxes", tax)
    doc.calculate_taxes_and_totals()
    return doc


@frappe.whitelist()
def bill(payload, save=0):
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    doc = _build(payload)
    if int(save):
        duplicates = frappe.get_list("Purchase Invoice", filters={"supplier": doc.supplier,
            "bill_no": doc.bill_no, "docstatus": ["<", 2]}, fields=["name", "docstatus"], limit_page_length=1)
        if duplicates:
            if duplicates[0].docstatus == 0:
                return {"name": duplicates[0].name, "existing": True}
            frappe.throw("This supplier already has a posted invoice with that number: " + duplicates[0].name)
        doc.insert()
        return {"name": doc.name, "existing": False}
    return {"net": flt(doc.net_total), "tax": flt(doc.total_taxes_and_charges), "total": flt(doc.grand_total),
            "due": rules.iso(doc.due_date), "terms": doc.payment_terms_template}
