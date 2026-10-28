"""ACC-PINV-2026-00594 (Elof Hansson): credit the USD 8,950.61 payment to the schedule (TRK-00147).

Payment Entry ACC-PAY-2026-04359 paid the taxes portion, but the Taxes row had no payment term, so ERPNext could not
match the payment to a row and both rows still showed as unpaid (they added up to 50,478.97 against an invoice
balance of 41,528.36). ERPNext refuses these edits on a submitted invoice through the API, so this sets them directly.

The Taxes row gets the "Import Taxes" term and is marked paid in full; the 0.69 left over from the payment is
credited to the net-total row. Idempotent and guarded: it changes nothing unless the invoice and both rows are
still exactly as they were found on 28/10/2026.
"""

INVOICE = "ACC-PINV-2026-00594"
NET_ROW = "bmgkvhfj0g"
TAX_ROW = "bmgks31j0d"
TERM = "Import Taxes"
RATE = 129.61            # the invoice's conversion rate
OUTSTANDING = 41528.36   # the invoice balance, which is the correct figure
PAID = 8950.61           # Payment Entry ACC-PAY-2026-04359


def corrected_rows(net_amount, tax_amount, paid=PAID, rate=RATE):
    """Paid and outstanding for the two rows, in document and base currency. The payment fills the taxes row first."""
    to_tax = min(paid, tax_amount)
    to_net = round(paid - to_tax, 4)
    net = {"paid_amount": to_net, "outstanding": round(net_amount - to_net, 4)}
    tax = {"paid_amount": round(to_tax, 4), "outstanding": round(tax_amount - to_tax, 4)}
    for r in (net, tax):
        r["base_paid_amount"] = round(r["paid_amount"] * rate, 4)
        r["base_outstanding"] = round(r["outstanding"] * rate, 4)
    return net, tax


def execute():
    import frappe

    if not frappe.db.exists("Purchase Invoice", INVOICE) or not frappe.db.exists("Payment Term", TERM):
        return
    inv = frappe.db.get_value("Purchase Invoice", INVOICE, ["docstatus", "outstanding_amount"], as_dict=True)
    net = frappe.db.get_value("Payment Schedule", NET_ROW, ["parent", "payment_amount", "paid_amount"], as_dict=True)
    tax = frappe.db.get_value("Payment Schedule", TAX_ROW, ["parent", "payment_amount", "paid_amount", "payment_term"], as_dict=True)
    if not (inv and net and tax) or inv.docstatus != 1 or net.parent != INVOICE or tax.parent != INVOICE:
        return
    if abs(float(inv.outstanding_amount) - OUTSTANDING) > 0.005:
        return                                    # the invoice moved on (a further payment): leave it alone
    if float(net.paid_amount or 0) or float(tax.paid_amount or 0):
        return                                    # already corrected
    if tax.payment_term not in (None, "", TERM):
        return
    net_vals, tax_vals = corrected_rows(float(net.payment_amount), float(tax.payment_amount))
    frappe.db.set_value("Payment Schedule", NET_ROW, net_vals, update_modified=False)
    frappe.db.set_value("Payment Schedule", TAX_ROW, {**tax_vals, "payment_term": TERM}, update_modified=False)
    frappe.clear_document_cache("Purchase Invoice", INVOICE)
