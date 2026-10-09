"""ACD reference code on import Purchase Invoices.

Two rules, both about the Advance Cargo Declaration reference:
  * an Importation invoice cannot be submitted without one (server side, so Desk and
    Compass are held to the same rule as the /purchasing-dev page);
  * every Monday, one email lists the import invoices that still have none, until the
    expected date of delivery to port has passed (that date is the "landed" signal we have).
"""

import frappe
from frappe.utils import add_days, get_url_to_form, getdate, nowdate

DEFAULT_RECIPIENTS = ["purchasing@vimit.com"]


def require_acd_before_submit(doc, method=None):
    if doc.get("custom_purchase_invoice_type") != "Importation":
        return
    if not (doc.get("custom_acd_reference_code") or "").strip():
        frappe.throw("Enter the ACD reference code before submitting an Importation invoice.",
                     title="ACD reference code missing")


def invoices_missing_acd(today=None):
    """Non-cancelled import invoices with no ACD code whose container has not yet landed."""
    today = getdate(today or nowdate())
    rows = frappe.get_all(
        "Purchase Invoice",
        filters={"custom_purchase_invoice_type": "Importation", "docstatus": ["<", 2],
                 "custom_acd_reference_code": ["in", ["", None]]},
        fields=["name", "supplier_name", "bill_no", "custom_bill_of_lading_number",
                "custom_expected_date_of_delivery_to_port", "docstatus"],
        order_by="custom_expected_date_of_delivery_to_port asc, name asc",
    )
    return [r for r in rows
            if not r.custom_expected_date_of_delivery_to_port
            or getdate(r.custom_expected_date_of_delivery_to_port) > today]


def build_email(rows):
    lines = "".join(
        "<tr><td><a href='{u}'>{n}</a></td><td>{s}</td><td>{b}</td><td>{bl}</td><td>{e}</td><td>{st}</td></tr>".format(
            u=get_url_to_form("Purchase Invoice", r.name), n=r.name, s=frappe.utils.escape_html(r.supplier_name or ""),
            b=frappe.utils.escape_html(r.bill_no or ""), bl=frappe.utils.escape_html(r.custom_bill_of_lading_number or ""),
            e=frappe.utils.formatdate(r.custom_expected_date_of_delivery_to_port) if r.custom_expected_date_of_delivery_to_port else "not set",
            st="Draft" if r.docstatus == 0 else "Submitted") for r in rows)
    return ("<p>These import invoices still have no <b>ACD reference code</b>. This reminder repeats every Monday "
            "until the code is entered or the container lands (expected date of delivery to port passes).</p>"
            "<table border='1' cellpadding='6' style='border-collapse:collapse'><tr><th>Invoice</th><th>Supplier</th>"
            "<th>Supplier inv.</th><th>Bill of lading</th><th>Expected at port</th><th>Status</th></tr>" + lines + "</table>")


def monday_reminder():
    """Scheduled Mondays 07:00. Sends nothing when nothing is outstanding."""
    rows = invoices_missing_acd()
    if not rows:
        return 0
    to = frappe.conf.get("acd_reminder_recipients") or DEFAULT_RECIPIENTS
    frappe.sendmail(recipients=to, subject="ACD reference code missing on %d import invoice%s" % (len(rows), "" if len(rows) == 1 else "s"),
                    message=build_email(rows))
    return len(rows)
