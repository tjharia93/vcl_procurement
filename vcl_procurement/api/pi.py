"""One purchase invoice, and the payments against it.

The server side of the invoice screen prototyped at /purchasing-dev (29-30 Sep
2026). The rules are in `rules.py`; this file reads and writes ERPNext.

**An import invoice cannot be submitted until the KRA details are in** - the
supplier invoice date, the KRA customs entry date and the KRA customs entry
number. That is enforced HERE, on the server, in two places: `pi_submit` (the
screen) and `gate_before_submit`, a `before_submit` document event (the Desk and
any script). A rule kept only in the browser would stop nothing.

**Ledger date.** An import invoice posts on the KRA customs entry date. The due
date still runs from the supplier invoice date plus terms.

**Local Purchase invoices** are submitted here too (from 30 Sep 2026) once they carry the
supplier invoice date and number. The KRA CUIN checks are ERPNext's own validate/submit events
(`vcl_kra_validation`), which run on `doc.submit()` exactly as they do in the Desk, so this API
keeps no second copy: a refusal comes back as the error text. Their payment schedule is one row,
the whole invoice, due per the supplier's terms from the supplier invoice date.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from vcl_procurement.api import rules
from vcl_procurement.api.purchasing import _assert_purchasing_role

PI = "Purchase Invoice"
PE = "Payment Entry"

INVOICE_TYPES = ["Local Purchase", "Importation", "Importation costs", "Other"]

_SIMPLE = {
    "bill_no": "bill_no",
    "bill_date": "bill_date",
    "custom_purchase_invoice_type": "custom_purchase_invoice_type",
    "custom_kra_import_number": "custom_kra_import_number",
    "custom_kra_entry_date": "custom_kra_entry_date",
    "custom_bill_of_lading_number": "custom_bill_of_lading_number",
    "custom_shipping_line": "custom_shipping_line",
    "custom_expected_date_of_delivery_to_port": "custom_expected_date_of_delivery_to_port",
    "remarks": "remarks",
}
_CONTAINER_FIELDS = ("container_reference", "description_of_packaging_eg_51_reels_or_515_packages",
                     "gross_weight", "net_weight", "container_size")


# --- the KRA gate, shared by the screen and the Desk --------------------------

def _missing(doc):
    """What still blocks a submit from the screen, for either kind of invoice."""
    return rules.pending_before_submit(doc.get("custom_purchase_invoice_type"), doc.get("bill_date"),
                                       doc.get("custom_kra_entry_date"), doc.get("custom_kra_import_number"),
                                       doc.get("bill_no"))


def gate_before_submit(doc, method=None):
    """`before_submit` on Purchase Invoice: an import invoice needs its KRA details."""
    if doc.get("is_return"):        # a debit note against an import invoice carries no KRA entry of its own
        return
    # Import-only in the Desk: a Local invoice's number/date rule is the screen's, not a new Desk block.
    missing = rules.kra_gate_missing(doc.get("custom_purchase_invoice_type"), doc.get("bill_date"),
                                     doc.get("custom_kra_entry_date"), doc.get("custom_kra_import_number"))
    if missing:
        frappe.throw(_("This importation invoice stays in Draft until these are filled:<br>{0}")
                     .format("<br>".join(missing)), title=_("Cannot submit: KRA details pending"))


# --- reading ------------------------------------------------------------------

def _term_row(template):
    """The first term of a payment terms template, with ERPNext's own fields."""
    if not template:
        return None, None
    t = frappe.get_doc("Payment Terms Template", template)
    if not t.terms:
        return None, None
    if len(t.terms) > 1:
        # A 30/70 template cannot become "net per terms": say so rather than silently use part one.
        raise ValueError(f"{template} has {len(t.terms)} parts; choose a single-part payment term.")
    r = t.terms[0]
    return {"due_date_based_on": r.due_date_based_on, "credit_days": r.credit_days,
            "credit_months": r.credit_months}, r.payment_term


def _linked_pos(doc):
    return sorted({i.purchase_order for i in doc.items if i.purchase_order})


def _from_po(doc, field):
    for po in _linked_pos(doc):
        v = frappe.db.get_value("Purchase Order", po, field)
        if v:
            return v
    return None


def _po_derived_type(doc):
    """The invoice type implied by the Purchase Order this invoice bills, or None when there is
    no PO (or it says nothing). The first linked PO that implies a type wins."""
    for po in _linked_pos(doc):
        d = frappe.db.get_value("Purchase Order", po, ["custom_order_type", "tax_category"], as_dict=True)
        t = rules.invoice_type_for_po(d.get("custom_order_type"), d.get("tax_category")) if d else None
        if t:
            return t
    return None


def _suggested_terms(doc):
    """Supplier default first, else the PO's. Says which, so the screen can too."""
    dflt = frappe.db.get_value("Supplier", doc.supplier, "payment_terms")
    if dflt:
        return dflt, "supplier default"
    po = _from_po(doc, "payment_terms_template")
    if po:
        return po, "from the PO (this supplier has no default)"
    return None, None


def _receive_from(doc):
    """Can goods be received against the PO this invoice bills? {po, ok, why}. Only meaningful for an
    invoice that booked no stock itself (update_stock off) - otherwise there is nothing to receive."""
    pos = _linked_pos(doc)
    if doc.docstatus == 2 or int(doc.update_stock or 0) or not pos:
        return None
    po = pos[0]
    d = frappe.db.get_value("Purchase Order", po, ["docstatus", "status", "per_received"], as_dict=True)
    if not d:
        return None
    ok, why = rules.po_open_for_receipt(d.docstatus, d.status, d.per_received)
    return {"po": po, "ok": ok, "why": why}


def _payments(name):
    """Payment Entries referencing this invoice, drafts included."""
    refs = frappe.get_all("Payment Entry Reference", filters={"reference_doctype": PI, "reference_name": name},
                          fields=["parent", "allocated_amount"], parent_doctype=PE, limit_page_length=200)
    names = sorted({r.parent for r in refs})
    if not names:
        return []
    heads = frappe.get_all(PE, filters={"name": ["in", names], "docstatus": ["<", 2]},
                           fields=["name", "posting_date", "paid_amount", "paid_from_account_currency",
                                   "mode_of_payment", "reference_no", "docstatus"],
                           order_by="posting_date desc", limit_page_length=200)
    applied = {r.parent: flt(r.allocated_amount) for r in refs}
    return [{"name": h.name, "date": rules.iso(h.posting_date), "paid": flt(h.paid_amount),
             "currency": h.paid_from_account_currency, "mode": h.mode_of_payment,
             "reference_no": h.reference_no, "draft": h.docstatus == 0,
             "applied": applied.get(h.name, 0)} for h in heads]


def _attachments(name):
    """Files on this invoice. Read server-side: the REST File list is broken on
    this site (a File permission hook names a doctype that no longer exists)."""
    try:
        return [{"name": f.name, "file_name": f.file_name, "file_url": f.file_url,
                 "size": f.file_size, "private": bool(f.is_private), "added": str(f.creation), "by": f.owner}
                for f in frappe.get_all("File", filters={"attached_to_doctype": PI, "attached_to_name": name},
                                        fields=["name", "file_name", "file_url", "file_size", "is_private",
                                                "creation", "owner"],
                                        order_by="creation desc", limit_page_length=200)]
    except Exception:
        return None


def _qbo(doc):
    out = {"status": doc.get("custom_qbo_sync_status"), "bill_id": doc.get("custom_qbo_bill_id"),
           "synced_at": str(doc.get("custom_qbo_synced_at")) if doc.get("custom_qbo_synced_at") else None,
           "queue": None}
    if frappe.db.exists("DocType", "QBO Bill Push Queue"):
        q = frappe.get_all("QBO Bill Push Queue", filters={"pi": doc.name, "docstatus": ["<", 2]},
                           fields=["name", "category", "push_status", "block_reason", "txn_date", "qbo_bill_id"],
                           order_by="creation desc", limit_page_length=1)
        if q:
            out["queue"] = {"name": q[0].name, "category": q[0].category, "push_status": q[0].push_status,
                            "block_reason": q[0].block_reason, "txn_date": rules.iso(q[0].txn_date),
                            "qbo_bill_id": q[0].qbo_bill_id}
    return out


@frappe.whitelist()
def pi_page(name):
    _assert_purchasing_role()
    doc = frappe.get_doc(PI, name)
    doc.check_permission("read")
    cur, rate = doc.currency, flt(doc.conversion_rate) or 1
    itype = doc.get("custom_purchase_invoice_type")
    draft = doc.docstatus == 0
    # A draft's type follows the order it bills; a submitted or cancelled invoice keeps what it was posted as.
    derived = _po_derived_type(doc) if draft else None
    if derived:
        itype = derived
    imp = rules.is_import(itype)

    taxes = [{
        "idx": t.idx, "name": t.name, "description": t.description, "account_head": t.account_head,
        "charge_type": t.charge_type, "rate": flt(t.rate), "row_id": t.row_id,
        "add_deduct_tax": t.add_deduct_tax or "Add", "category": t.category,
        "tax_amount": flt(t.tax_amount),
        "kes": flt(t.base_tax_amount) if t.base_tax_amount is not None else rules.doc_to_kes(t.tax_amount, rate, cur),
    } for t in doc.get("taxes") or []]

    sug, sug_src = _suggested_terms(doc)
    schedule = [{
        "payment_term": p.payment_term, "description": p.description, "due": rules.iso(p.due_date),
        "portion": flt(p.invoice_portion), "amount": flt(p.payment_amount), "outstanding": flt(p.outstanding),
    } for p in doc.get("payment_schedule") or []]
    # An import keeps two rows (net + taxes); a local invoice keeps one.
    saved_term = schedule[0]["payment_term"] if len(schedule) >= (2 if imp else 1) else None

    missing = _missing(doc)
    outstanding = flt(doc.outstanding_amount)
    return {
        "name": doc.name, "docstatus": doc.docstatus, "status": doc.status,
        "supplier": doc.supplier, "supplier_name": doc.supplier_name or doc.supplier,
        "currency": cur, "conversion_rate": rate, "company": doc.company,
        "invoice_type": itype, "is_import": imp, "type_follows_po": bool(derived),
        "bill_no": doc.bill_no,
        "dates": {"bill_date": rules.iso(doc.bill_date), "kra_entry_date": rules.iso(doc.get("custom_kra_entry_date")),
                  "posting_date": rules.iso(doc.posting_date), "due_date": rules.iso(doc.due_date),
                  "clearance_date": rules.iso(doc.get("clearance_date"))},
        "kra_entry_number": doc.get("custom_kra_import_number"),
        # Filled from the linked PO and not edited on the invoice.
        "proforma_no": _from_po(doc, "order_confirmation_no") or doc.get("custom_proforma_invoice_number_importation_link"),
        "file_no": _from_po(doc, "custom_file_number") or doc.get("custom_file_number"),
        "purchase_orders": _linked_pos(doc),
        "update_stock": int(doc.update_stock or 0),
        "shipment": {
            "bill_of_lading_no": doc.get("custom_bill_of_lading_number"),
            "shipping_line": doc.get("custom_shipping_line"),
            "expected_delivery_to_port": rules.iso(doc.get("custom_expected_date_of_delivery_to_port")),
            "days_free": flt(doc.get("custom_number_of_days_free")),
            "containers": [{"name": c.name, "container_reference": c.container_reference,
                            "packaging": c.description_of_packaging_eg_51_reels_or_515_packages,
                            "gross_weight": flt(c.gross_weight), "net_weight": flt(c.net_weight),
                            "container_size": c.container_size} for c in doc.get("custom_container_details_table") or []],
        },
        "lines": [{"name": i.name, "item_code": i.item_code, "item_name": i.item_name or i.item_code,
                   "qty": flt(i.qty), "uom": i.uom, "rate": flt(i.rate), "amount": flt(i.amount),
                   "warehouse": i.warehouse, "expense_account": i.expense_account,
                   "purchase_order": i.purchase_order} for i in doc.items],
        "net_total": flt(doc.net_total), "base_net_total": flt(doc.base_net_total),
        "taxes": taxes, "total_tax_kes": rules.signed_sum(taxes, "kes"),
        "taxes_all_actual": bool(taxes) and all(t["charge_type"] == "Actual" for t in taxes),
        "grand_total": flt(doc.grand_total), "base_grand_total": flt(doc.base_grand_total),
        "outstanding": outstanding,
        "schedule": schedule,
        "schedule_terms": {"selected": saved_term or sug, "source": "saved on this invoice" if saved_term else sug_src,
                           "options": frappe.get_all("Payment Terms Template", pluck="name", order_by="name asc",
                                                     limit_page_length=200) if draft else [],
                           "tax_days_after_landing": rules.DEFAULT_TAX_DAYS_AFTER_LANDING},
        "payments": _payments(doc.name),
        "qbo": _qbo(doc),
        "attachments": _attachments(doc.name),
        "requirements": {"missing": missing, "ready": not missing},
        "receive_from": _receive_from(doc),
        "invoice_types": INVOICE_TYPES,
        "can": {
            "edit": draft,
            "submit": draft and not missing,
            "submit_here": True,
            "pay": doc.docstatus == 1 and outstanding > 0.005,
        },
        "desk_url": f"/app/purchase-invoice/{doc.name}",
    }


# --- writing ------------------------------------------------------------------

@frappe.whitelist()
def pi_save(name, payload):
    """Save an edit to a DRAFT purchase invoice. Returns {page, notes}.

    payload keys (all optional): bill_no, bill_date, custom_purchase_invoice_type,
    custom_kra_import_number, custom_kra_entry_date, posting_date (non-import only),
    conversion_rate, update_stock, remarks, shipment fields, containers[], lines
    [{name, qty, rate}], taxes {mode: "actual"|"calculated", rows: [{name, kes}]},
    schedule {terms_template, tax_days_after_landing}.
    """
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    doc = frappe.get_doc(PI, name)
    doc.check_permission("write")
    if doc.docstatus != 0:
        frappe.throw(_("{0} is not a draft, so it cannot be edited.").format(name))
    notes = []

    data = {}
    for key in _SIMPLE:
        if key in payload:
            data[_SIMPLE[key]] = payload[key] or None
    if "custom_number_of_days_free" in payload:
        data["custom_number_of_days_free"] = flt(payload["custom_number_of_days_free"])
    if "update_stock" in payload:
        data["update_stock"] = 1 if payload["update_stock"] else 0
    if "conversion_rate" in payload:
        if flt(payload["conversion_rate"]) <= 0:
            frappe.throw(_("The exchange rate must be greater than zero."))
        data["conversion_rate"] = flt(payload["conversion_rate"])

    # The type follows the order being billed: whatever the payload says, a PO-derived type wins.
    derived = _po_derived_type(doc)
    if derived:
        data["custom_purchase_invoice_type"] = derived
    itype = data.get("custom_purchase_invoice_type", doc.get("custom_purchase_invoice_type"))
    if itype and itype not in INVOICE_TYPES:
        frappe.throw(_("Unknown invoice type: {0}").format(itype))
    kra_date = data["custom_kra_entry_date"] if "custom_kra_entry_date" in data else doc.get("custom_kra_entry_date")
    bill_date = data["bill_date"] if "bill_date" in data else doc.bill_date

    # Ledger date: an import posts on the KRA customs entry date; nothing else moves it.
    if rules.is_import(itype):
        posting = rules.posting_date_for(itype, kra_date, doc.posting_date)
    else:
        posting = rules.iso(payload["posting_date"]) if payload.get("posting_date") else rules.iso(doc.posting_date)
    if posting and posting != rules.iso(doc.posting_date):
        data["posting_date"] = posting
    # ERPNext only honours a posting date other than today when this is ticked.
    if posting:
        data["set_posting_time"] = 0 if posting == rules.iso(nowdate()) else 1

    if "containers" in payload:
        data["custom_container_details_table"] = [
            {**{k: c.get(k) for k in _CONTAINER_FIELDS},
             "gross_weight": flt(c.get("gross_weight")), "net_weight": flt(c.get("net_weight"))}
            for c in (payload["containers"] or [])]

    if "lines" in payload:
        want = {ln["name"]: ln for ln in payload["lines"] if ln.get("name")}
        rows = []
        for i in doc.items:
            row = i.as_dict()
            if i.name in want:
                row["qty"] = flt(want[i.name].get("qty"))
                rate_ = flt(want[i.name].get("rate"))
                row["rate"] = rate_
                # `rate` is the only price field: keep ERPNext from re-deriving it (see po_price_guard).
                row.update(price_list_rate=rate_, base_price_list_rate=rate_, margin_type="", rate_with_margin=0,
                           base_rate_with_margin=0, margin_rate_or_amount=0, discount_percentage=0, discount_amount=0)
            rows.append(row)
        data["items"] = rows

    tx = payload.get("taxes") or {}
    if tx.get("mode") == "actual":
        rate = flt(data.get("conversion_rate") or doc.conversion_rate) or 1
        kes_by_name = {r["name"]: r.get("kes") for r in tx.get("rows") or [] if r.get("name")}
        rows = []
        for t in doc.get("taxes") or []:
            row = t.as_dict()
            row["charge_type"] = "Actual"
            row["row_id"] = None
            if t.name in kes_by_name:
                row["tax_amount"] = rules.kes_to_doc(kes_by_name[t.name], rate, doc.currency)
            rows.append(row)
        data["taxes"] = rows

    doc.update(data)

    sched = payload.get("schedule")
    if sched and not rules.is_import(itype):
        doc.calculate_taxes_and_totals()
        try:
            term, term_name = _term_row(sched.get("terms_template"))
        except ValueError as e:
            frappe.throw(str(e))
        total = flt(doc.rounded_total) or flt(doc.grand_total)
        rows, missing = rules.local_schedule(total, bill_date, term, term_name)
        if missing:
            notes.append("The payment schedule was not updated: it still needs the " + ", ".join(missing) + ".")
        else:
            doc.payment_terms_template = None
            doc.set("payment_schedule", rows)
    elif sched and rules.is_import(itype):
        doc.calculate_taxes_and_totals()
        try:
            term, term_name = _term_row(sched.get("terms_template"))
        except ValueError as e:
            frappe.throw(str(e))
        net = flt(doc.net_total)
        # The schedule must add up to what ERPNext will compare it with.
        total = flt(doc.rounded_total) or flt(doc.grand_total)
        rows, missing = rules.import_schedule(
            net, total - net, bill_date, term, term_name,
            doc.get("custom_expected_date_of_delivery_to_port"),
            int(sched.get("tax_days_after_landing", rules.DEFAULT_TAX_DAYS_AFTER_LANDING)))
        if missing:
            notes.append("The payment schedule was not updated: it still needs the " + ", ".join(missing) + ".")
        else:
            # A template on the invoice would make ERPNext rebuild the schedule from it and discard these rows.
            doc.payment_terms_template = None
            doc.set("payment_schedule", rows)

    doc.save()
    return {"page": pi_page(name), "notes": notes}


@frappe.whitelist()
def schedule_preview(name, payload):
    """The import payment schedule as it WOULD be, without saving anything.

    Keeps the calculation on the server (rules.import_schedule) so the screen never
    holds a second copy of it. payload: {terms_template, tax_days_after_landing,
    bill_date, expected_delivery, taxes_kes?}. Anything omitted is read from the invoice;
    `taxes_kes` lets the screen preview typed-but-unsaved tax amounts.
    """
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    doc = frappe.get_doc(PI, name)
    doc.check_permission("read")
    try:
        term, term_name = _term_row(payload.get("terms_template"))
    except ValueError as e:
        return {"rows": None, "missing": [str(e)], "kes": None}
    if not rules.is_import(payload.get("invoice_type") or doc.get("custom_purchase_invoice_type")):
        total = flt(doc.rounded_total) or flt(doc.grand_total)
        rows, missing = rules.local_schedule(total, payload.get("bill_date") or doc.bill_date, term, term_name)
        return {"rows": rows, "missing": missing,
                "kes": None if not rows else [rules.doc_to_kes(r["payment_amount"], doc.conversion_rate, doc.currency) for r in rows]}
    net = flt(doc.net_total)
    if payload.get("taxes_kes") is not None:
        taxes = rules.kes_to_doc(payload["taxes_kes"], doc.conversion_rate, doc.currency)
    else:
        taxes = (flt(doc.rounded_total) or flt(doc.grand_total)) - net
    bill = payload.get("bill_date") or doc.bill_date
    eta = payload.get("expected_delivery") or doc.get("custom_expected_date_of_delivery_to_port")
    days = int(payload.get("tax_days_after_landing", rules.DEFAULT_TAX_DAYS_AFTER_LANDING))
    rows, missing = rules.import_schedule(net, taxes, bill, term, term_name, eta, days)
    return {"rows": rows, "missing": missing,
            "kes": None if not rows else [rules.doc_to_kes(r["payment_amount"], doc.conversion_rate, doc.currency) for r in rows]}


@frappe.whitelist()
def pi_set_stock(name, on):
    """The Receive goods tick: books stock with the invoice (1) or ledger only, pending receipt (0)."""
    _assert_purchasing_role()
    doc = frappe.get_doc(PI, name)
    doc.check_permission("write")
    if doc.docstatus != 0:
        frappe.throw(_("{0} is not a draft.").format(name))
    doc.update_stock = 1 if int(on) else 0
    doc.save()
    return {"update_stock": int(doc.update_stock)}


@frappe.whitelist()
def pi_submit(name):
    """Submit an invoice, if its pending details are in. Returns the fresh page.

    Import: bill date + KRA entry date + KRA entry number. Local: bill date + supplier invoice no.
    ERPNext's own submit events (the KRA CUIN checks) still run and their refusal is the error.
    """
    _assert_purchasing_role()
    doc = frappe.get_doc(PI, name)
    doc.check_permission("submit")
    if doc.docstatus != 0:
        frappe.throw(_("{0} is not a draft.").format(name))
    missing = _missing(doc)
    if missing:
        frappe.throw(_("Not submitted. Still pending:<br>{0}").format("<br>".join(missing)))
    gate_before_submit(doc)   # the same rule the before_submit event applies
    doc.submit()
    return pi_page(name)


@frappe.whitelist()
def bill_preview(po):
    """What the Bill screen shows: the order, and the unbilled balance line by line.

    Read from the PO. `left` is amount - billed_amt before tax, in the order's currency.
    """
    _assert_purchasing_role()
    doc = frappe.get_doc("Purchase Order", po)
    doc.check_permission("read")
    if doc.docstatus != 1:
        frappe.throw(_("{0} is not approved, so it cannot be billed.").format(po))
    left = {r["idx"]: r["left"] for r in rules.unbilled_lines(
        [{"idx": i.idx, "amount": i.amount, "billed_amt": i.billed_amt} for i in doc.items])}
    return {
        "po": doc.name,
        "supplier": doc.supplier,
        "supplier_name": doc.supplier_name or doc.supplier,
        "currency": doc.currency,
        "status": doc.status,
        "grand_total": flt(doc.grand_total),
        "transaction_date": str(doc.transaction_date) if doc.transaction_date else None,
        "order_type": doc.get("custom_order_type"),
        "payment_terms_template": doc.get("payment_terms_template"),
        "per_received": flt(doc.per_received),
        "per_billed": flt(doc.per_billed),
        "lines": [{
            "idx": i.idx, "item_code": i.item_code, "item_name": i.item_name, "uom": i.uom,
            "qty": flt(i.qty), "received_qty": flt(i.received_qty), "rate": flt(i.rate), "left": left[i.idx],
        } for i in doc.items if i.idx in left],
        "left_total": round(sum(left.values()), 2),
    }


@frappe.whitelist()
def bill_from_po(po, bill_date=None, bill_no=None):
    """A DRAFT purchase invoice for the unbilled balance of a purchase order.

    The supplier's invoice date is required: it sets the due date, and (for an
    import) it is one of the three things the KRA gate asks for. The number is
    optional and can be added on the invoice page.
    """
    _assert_purchasing_role()
    if not bill_date:
        frappe.throw(_("Enter the supplier invoice date."))
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice
    pi = make_purchase_invoice(po)
    pi.bill_date = bill_date
    if (bill_no or "").strip():
        pi.bill_no = bill_no.strip()

    src = frappe.db.get_value("Purchase Order", po, ["custom_order_type", "tax_category", "custom_file_number"],
                              as_dict=True) or {}
    itype = rules.invoice_type_for_po(src.get("custom_order_type"), src.get("tax_category"))
    if itype:
        pi.custom_purchase_invoice_type = itype
    if src.get("custom_file_number") and not pi.get("custom_file_number"):
        pi.custom_file_number = src["custom_file_number"]

    # The PO can carry a stale rate (even 1) for a foreign currency: price the bill at the day's rate.
    company_currency = frappe.get_cached_value("Company", pi.company, "default_currency")
    if pi.currency and company_currency and pi.currency != company_currency:
        try:
            from erpnext.setup.utils import get_exchange_rate
            looked_up = get_exchange_rate(pi.currency, company_currency, pi.get("posting_date") or nowdate())
        except Exception:
            looked_up = 0      # keep the PO's rate rather than fail the bill
        pi.conversion_rate = rules.rate_after_lookup(pi.conversion_rate, looked_up)

    pi.insert()
    return {"name": pi.name, "docstatus": pi.docstatus, "po": po,
            "invoice_type": pi.get("custom_purchase_invoice_type"),
            "conversion_rate": flt(pi.conversion_rate) or 1}


# --- payments -----------------------------------------------------------------

@frappe.whitelist()
def pay_context(pi):
    """What the Record payment screen needs: the accounts, the modes, the rows still to pay."""
    _assert_purchasing_role()
    doc = frappe.get_doc(PI, pi)
    doc.check_permission("read")
    accounts = frappe.get_all("Account", filters={"account_type": ["in", ["Bank", "Cash"]], "is_group": 0,
                                                  "company": doc.company},
                              fields=["name", "account_currency"], order_by="name asc", limit_page_length=200)
    modes = frappe.get_all("Mode of Payment", pluck="name", order_by="name asc", limit_page_length=200)
    page = pi_page(pi)
    return {
        "invoice": page,
        "accounts": [{"name": a.name, "currency": a.account_currency} for a in accounts],
        "modes": modes,
        "rows": [r for r in page["schedule"] if r["outstanding"] > 0.005],
    }


def _build_payment(pi_doc, payload):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    account, amount = payload.get("account"), flt(payload.get("amount"))
    if not account:
        frappe.throw(_("Choose the account the payment was made from."))
    if amount <= 0:
        frappe.throw(_("Enter the amount paid."))
    acct_cur = frappe.db.get_value("Account", account, "account_currency")
    fx = acct_cur != pi_doc.currency
    rate = flt(payload.get("rate")) or flt(pi_doc.conversion_rate) or 1
    if not fx:
        applied = amount
    elif acct_cur == rules.COMPANY_CURRENCY:        # KES paid against a foreign-currency invoice
        applied = amount / rate
    elif pi_doc.currency == rules.COMPANY_CURRENCY:  # a foreign account paying a KES invoice
        applied = amount * rate
    else:
        frappe.throw(_("Pay from an account in {0} or in {1}.").format(rules.COMPANY_CURRENCY, pi_doc.currency))
    out = flt(pi_doc.outstanding_amount)
    if applied > out + 0.01:
        frappe.throw(_("That is more than the outstanding {0} {1}.").format(pi_doc.currency, out))

    pe = get_payment_entry(PI, pi_doc.name, party_amount=round(applied, 2), bank_account=account, bank_amount=amount)
    pe.posting_date = rules.iso(payload.get("date")) or nowdate()
    pe.mode_of_payment = payload.get("mode") or None
    pe.reference_no = payload.get("reference_no") or None
    pe.reference_date = rules.iso(payload.get("reference_date")) or pe.posting_date
    label = payload.get("what") or "invoice"
    pe.remarks = ((payload.get("remarks") + " · ") if payload.get("remarks") else "") + f"Payment of {label} on {pi_doc.name}"
    # Two currencies: make both sides agree so there is no unexplained difference;
    # ERPNext books any exchange gain or loss against the invoice.
    if pe.paid_from_account_currency != pe.paid_to_account_currency and flt(pe.received_amount):
        pe.target_exchange_rate = (flt(pe.paid_amount) * flt(pe.source_exchange_rate or 1)) / flt(pe.received_amount)
    if payload.get("payment_term") and pe.references:
        pe.references[0].payment_term = payload["payment_term"]
    return pe, applied


@frappe.whitelist()
def pay_create(pi, payload, submit=0):
    """Record a payment against a SUBMITTED invoice.

    Creates the Payment Entry as a draft and commits it, then submits it when
    asked. If the submit fails the draft stays, and the answer says so - the
    person is never left wondering whether money was recorded.
    """
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    doc = frappe.get_doc(PI, pi)
    doc.check_permission("read")
    if doc.docstatus != 1:
        frappe.throw(_("Submit the invoice first: a payment can only be recorded against a submitted invoice."))

    pe, applied = _build_payment(doc, payload)
    # A double-click must not record the payment twice: reuse a draft made in the last few
    # minutes for the same invoice, amount and reference.
    dup = _recent_duplicate(doc.name, pe)
    if dup:
        pe = frappe.get_doc(PE, dup)
    else:
        pe.insert()
        frappe.db.commit()
    result = {"name": pe.name, "docstatus": pe.docstatus, "applied": applied, "posted": False, "error": None}
    if int(submit):
        try:
            pe.submit()
            result.update(docstatus=pe.docstatus, posted=pe.docstatus == 1)
        except Exception as e:  # keep the draft; report plainly
            frappe.db.rollback()
            frappe.clear_messages()      # the answer carries the error; do not also raise a popup
            result["error"] = str(e)
    return result


def _recent_duplicate(pi_name, pe):
    rows = frappe.db.sql(
        """select pe.name from `tabPayment Entry` pe
           join `tabPayment Entry Reference` r on r.parent = pe.name
           where pe.docstatus = 0 and r.reference_doctype = %s and r.reference_name = %s
             and pe.paid_amount = %s and ifnull(pe.reference_no, '') = %s
             and pe.creation > now() - interval 5 minute
           order by pe.creation desc limit 1""",
        (PI, pi_name, flt(pe.paid_amount), pe.reference_no or ""))
    return rows[0][0] if rows else None


@frappe.whitelist()
def pay_submit(name):
    """Submit a draft Payment Entry that was saved earlier."""
    _assert_purchasing_role()
    pe = frappe.get_doc(PE, name)
    pe.check_permission("submit")
    if pe.docstatus != 0:
        frappe.throw(_("{0} is not a draft.").format(name))
    # Only a supplier payment against purchase invoices: this API must not be a back door
    # to submit customer receipts or internal transfers.
    if pe.party_type != "Supplier" or not pe.references or any(r.reference_doctype != PI for r in pe.references):
        frappe.throw(_("{0} is not a supplier payment against purchase invoices.").format(name))
    pe.submit()
    return {"name": pe.name, "docstatus": pe.docstatus, "posted": pe.docstatus == 1}
