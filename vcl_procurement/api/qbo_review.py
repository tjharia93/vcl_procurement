"""The checks shared by the initial Compass push and retry proxies."""
import frappe
from frappe.utils import flt
from vcl_procurement.api import rules


def prepare_queue(name, date_reviewed=0):
    from vcl_procurement.api.pi import qbo_prepare
    queue = frappe.get_doc("QBO Bill Push Queue", name)
    queue.check_permission("read")
    if queue.docstatus == 2:
        frappe.throw("A cancelled queue row cannot be pushed.")
    if not queue.get("pi"):
        frappe.throw("The queue row has no Purchase Invoice.")
    invoice = frappe.get_doc("Purchase Invoice", queue.pi)
    invoice.check_permission("read")
    if invoice.docstatus != 1:
        frappe.throw("Only a submitted Purchase Invoice can be pushed.")
    errors = rules.qbo_payment_errors(queue, invoice.base_grand_total, invoice.conversion_rate)
    if queue.get("qbo_txn_type") == "Expense" and queue.get("pay_bank_account"):
        bank = frappe.get_doc("QBO Account", queue.pay_bank_account)
        bank.check_permission("read")
        if not bank.active or bank.account_type not in ("Bank", "Credit Card"):
            errors.append("Choose an active QuickBooks bank or credit-card account.")
    if queue.get("vat_line_manual") and queue.get("vat_line_account"):
        account = frappe.get_doc("QBO Account", queue.vat_line_account)
        account.check_permission("read")
        if not account.active or account.account_sub_type in ("GlobalTaxPayable", "GlobalTaxSuspense"):
            errors.append("QuickBooks tax-control accounts cannot take a typed VAT line.")
    if errors:
        frappe.throw("<br>".join(errors), title="QuickBooks review incomplete")
    # Applies the existing KRA/date policy and records the same review audit on every push path.
    qbo_prepare(invoice.name, date_reviewed=date_reviewed)
