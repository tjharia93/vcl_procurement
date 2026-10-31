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
    "custom_acd_reference_code": "custom_acd_reference_code",
    "custom_supplier_reference_no": "custom_supplier_reference_no",
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


def _receipt_check(doc):
    """CR-006: per invoice line billed from a PO line, what the PO has received and billed, judged by rules.receipt_cover.
    Also the submitted Purchase Receipts against the invoice's orders. None on a cancelled invoice."""
    if doc.docstatus == 2:
        return None
    lines = []
    for i in doc.items:
        if not i.get("po_detail"):
            continue
        d = frappe.db.get_value("Purchase Order Item", i.po_detail, ["qty", "received_qty", "billed_amt", "rate"], as_dict=True)
        if not d:
            continue
        rate = flt(d.rate)
        # Submitted invoices only; once this one is submitted its own amount is in billed_amt, so take it back out.
        before = flt(d.billed_amt) / rate if rate else 0.0
        if doc.docstatus == 1:
            before = max(0.0, before - flt(i.qty))
        lines.append({"item": i.item_name or i.item_code, "bill_qty": flt(i.qty), "po_qty": flt(d.qty),
                      "received": flt(d.received_qty), "billed_before": round(before, 6), "uom": i.uom})
    out = rules.receipt_cover(lines, doc.update_stock)
    pos = _linked_pos(doc)
    out["receipts"] = sorted({r.parent for r in frappe.get_all("Purchase Receipt Item", filters={"purchase_order": ["in", pos], "docstatus": 1},
                                                                fields=["parent"], limit_page_length=50)}) if pos else []
    return out


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
                           fields=["name", "category", "push_status", "block_reason", "txn_date", "qbo_bill_id", "docstatus", "error_message"],
                           order_by="creation desc", limit_page_length=1)
        if q:
            out["queue"] = {"name": q[0].name, "category": q[0].category, "push_status": q[0].push_status,
                            "block_reason": q[0].block_reason, "txn_date": rules.iso(q[0].txn_date),
                            "qbo_bill_id": q[0].qbo_bill_id, "docstatus": q[0].docstatus,
                            "error_message": q[0].error_message}
            out["review"] = _qbo_review(doc, q[0].name)
    if not out["queue"] and doc.docstatus == 1:
        out["stage_hint"] = _stage_hint(doc)
    return out


def _stage_hint(doc):
    """Why a submitted invoice has no queue row (DEF-001), in words for the screen. None when nothing explains it."""
    try:
        st = frappe.get_cached_doc("QBO Sync Settings")
    except Exception:
        return None
    return rules.stage_hint(st.get("staging_enabled"), st.get("earliest_posting_date"), doc.posting_date)


@frappe.whitelist()
def qbo_history(name):
    """CR-009: the last three successful QuickBooks postings of each item on this invoice, for the same supplier.

    Suggestions only: nothing is applied here. Successful = the queue row was submitted, has a QuickBooks bill id,
    did not fail, and its invoice is not cancelled. A destination no longer active in QuickBooks is left out.
    Returns {scope, items: {erp_item: [...]}}.
    """
    _assert_purchasing_role()
    doc = frappe.get_doc(PI, name)
    doc.check_permission("read")
    items = sorted({i.item_code for i in doc.items if i.item_code})
    QD, QL = "QBO Bill Push Queue", "QBO Bill Push Queue Line"
    if not items or not frappe.db.exists("DocType", QD):
        return {"scope": "same supplier and item", "items": {i: [] for i in items}}
    rows = frappe.get_all(QD, filters={"supplier": doc.supplier, "docstatus": 1, "qbo_bill_id": ["is", "set"], "pi": ["!=", doc.name]},
                          fields=["name", "pi", "txn_date", "push_status", "tax_qbo_taxcode"], order_by="txn_date desc, creation desc",
                          limit_page_length=60)
    cancelled = {r.name for r in frappe.get_all(PI, filters={"name": ["in", [r.pi for r in rows if r.pi] or ["-"]], "docstatus": 2}, fields=["name"])}
    parents = [{"name": r.name, "pi": r.pi, "date": r.txn_date, "taxcode": r.tax_qbo_taxcode} for r in rows
               if (r.push_status or "") != "Failed" and r.pi not in cancelled]
    lines = frappe.get_all(QL, filters={"parent": ["in", [p["name"] for p in parents] or ["-"]], "parenttype": QD, "erp_item_code": ["in", items]},
                           fields=["parent", "erp_item_code", "routing_kind", "qbo_item_override as item_id", "qbo_item_name as item_name",
                                   "qbo_account_override as account_id", "qbo_account_name as account_name", "qbo_taxcode_override as taxcode"],
                           parent_doctype=QD, limit_page_length=500) if parents else []
    cache = {}

    def active(kind, did):
        key = (kind, did)
        if key not in cache:
            dt = "QBO Item" if kind == "Item" else "QBO Account"
            v = frappe.db.get_value(dt, did, "active") if frappe.db.exists(dt, did) else None
            cache[key] = bool(v)
        return cache[key]
    return {"scope": "same supplier and item", "items": rules.mapping_history(parents, [dict(l) for l in lines], items, active)}


def _qbo_review(doc, queue_name):
    """What will be posted to QuickBooks, read off the queue row, with the checks that gate the push."""
    try:
        q = frappe.get_doc("QBO Bill Push Queue", queue_name)
    except Exception:
        return None
    itype = doc.get("custom_purchase_invoice_type")
    docnumber, source = rules.qbo_docnumber(itype, doc.get("custom_kra_import_number"), doc.bill_no, q.get("qbo_docnumber_override"))
    vendor = None
    try:
        if q.get("vendor_override"):
            vendor = {"name": q.get("vendor_qbo_name") or q.vendor_override, "approved": 1, "source": "typed on the queue row"}
        else:
            from vcl_procurement.api import resolvers
            v = resolvers.resolve_supplier(doc.supplier)
            if v:
                vendor = {"name": v.get("qbo_name"), "approved": 1 if v.get("approved") else 0, "source": "vendor map"}
    except Exception:
        vendor = None
    lines = []
    for l in q.get("lines") or []:
        kind = l.get("routing_kind") or ""
        lines.append({"idx": l.get("line_idx"), "description": (l.get("description") or "").strip(), "qty": flt(l.get("qty")),
                      "rate": flt(l.get("rate")), "amount": flt(l.get("amount")), "erp_item": l.get("erp_item_code"),
                      "item": l.get("qbo_item_name") or l.get("qbo_item_override") or None,
                      "account": l.get("qbo_account_name") or l.get("qbo_account_override") or None,
                      "routing": kind, "taxcode": l.get("qbo_taxcode_override") or q.get("tax_qbo_taxcode")})
    txn = rules.qbo_txn_date(itype, doc.get("custom_kra_entry_date"), q.get("txn_date") or doc.get("bill_date"), doc.posting_date)
    checks = rules.qbo_checks(docnumber, str(txn) if txn else None, vendor, lines, flt(doc.get("net_total") or doc.get("total")), q.get("category"))
    return {"queue": q.name, "is_import": rules.is_import(itype), "docnumber": docnumber, "docnumber_source": source, "docnumber_typed": (q.get("qbo_docnumber_override") or "").strip(),
            "txn_date": rules.iso(txn), "currency": doc.currency, "rate": flt(doc.conversion_rate) or 1, "memo": q.get("qbo_memo"),
            "vendor": vendor, "tax_calculation": q.get("tax_calculation"), "lines": lines,
            "lines_total": round(sum(l["amount"] for l in lines), 2), "pi_net": flt(doc.get("net_total") or doc.get("total")),
            "checks": checks, "ready": all(c["ok"] for c in checks)}


def _memo_for(doc, memo):
    """The memo to write on the queue row, or None to leave it as it is."""
    pos = _linked_pos(doc)
    if rules.memo_has_refs(memo, doc.name, pos):
        return None
    proforma = _from_po(doc, "order_confirmation_no") or doc.get("custom_proforma_invoice_number_importation_link")
    file_number, _src = rules.file_no(_from_po(doc, "custom_file_number"), doc.get("custom_file_number"), proforma)
    if not (memo or "").strip():
        return rules.qbo_memo(doc.name, doc.bill_no, pos, proforma, file_number, doc.get("custom_bill_of_lading_number"),
                              [c.container_reference for c in doc.get("custom_container_details_table") or []],
                              doc.currency, doc.get("net_total"), doc.get("conversion_rate"), doc.get("bill_date") or doc.get("posting_date"))
    refs = rules.qbo_memo(doc.name, None, [p for p in pos if p not in memo], None, None)
    refs = " · ".join(x for x in refs.split(" · ") if x not in memo)
    return (memo.strip() + " · " + refs)[:4000] if refs else None


@frappe.whitelist(methods=["POST"])
def qbo_prepare(name, date_reviewed=0):
    """Before a push: put the right bill number on the draft queue row (an import uses the KRA customs entry number)
    and the order / invoice references in its memo (every invoice).

    A local invoice whose bill date fails the date rule against KRA's invoice date (CR-010) is refused
    until the person pushing says the bill date has been reviewed (`date_reviewed=1`); that is recorded
    as a comment on the invoice. If KRA does not answer the date stays unverified and does not block.

    Never touches a row that has been submitted. Returns the refreshed page.
    """
    _assert_purchasing_role()
    doc = frappe.get_doc(PI, name)
    doc.check_permission("read")
    if not rules.is_import(doc.get("custom_purchase_invoice_type")):
        review = _date_check(doc)
        if review.get("block"):
            frappe.throw(_("{0} KRA's invoice date is {1}; the bill date is {2}. Correct the bill date before pushing.").format(
                review["text"], rules.dmy(review["kra_date"]), rules.dmy(review["bill_date"])), title=_("Bill date refused"))
        if review["review"]:
            if not int(date_reviewed or 0):
                frappe.throw(_("{0} KRA's invoice date is {1}; the bill date is {2}. Correct the bill date, or tick "
                               "\"Bill date reviewed\" in the push review to push it as it is.").format(
                    review["text"], rules.dmy(review["kra_date"]), rules.dmy(review["bill_date"])),
                    title=_("Bill date needs review"))
            doc.add_comment("Comment", _("Bill date reviewed before the QuickBooks push: KRA invoice date {0}, bill date {1} ({2}).")
                            .format(rules.dmy(review["kra_date"]), rules.dmy(review["bill_date"]), review["state"].replace("_", " ")))
    row = frappe.get_all("QBO Bill Push Queue", filters={"pi": name, "docstatus": 0}, fields=["name", "qbo_docnumber_override", "txn_date", "qbo_memo"], limit_page_length=1)
    if row:
        # Every bill carries its order and invoice references in the memo (cr-mvqewpg9): a blank memo
        # gets the standard one, a typed one that lacks them gets them added at the end.
        memo = _memo_for(doc, row[0].qbo_memo)
        if memo is not None:
            frappe.db.set_value("QBO Bill Push Queue", row[0].name, "qbo_memo", memo, update_modified=True)
    if row and rules.is_import(doc.get("custom_purchase_invoice_type")):
        want = (doc.get("custom_kra_import_number") or "").strip()
        when = rules.qbo_txn_date(doc.get("custom_purchase_invoice_type"), doc.get("custom_kra_entry_date"), None, None)
        if not want:
            frappe.throw("This is an import: the KRA customs entry number is missing, and QuickBooks needs it as the bill number.")
        if not when:
            frappe.throw("This is an import: the KRA customs entry date is missing, and QuickBooks is dated on it.")
        if (row[0].qbo_docnumber_override or "").strip() != want:
            frappe.db.set_value("QBO Bill Push Queue", row[0].name, "qbo_docnumber_override", want, update_modified=True)
        if str(row[0].txn_date or "")[:10] != str(when)[:10]:
            frappe.db.set_value("QBO Bill Push Queue", row[0].name, "txn_date", when, update_modified=True)
    return pi_page(name)


# --- KRA's view of a local invoice, and the date rule (CR-010) -----------------------

def _kra_lookup(cuin):
    """What KRA's iTax holds for a CUIN, through vcl_kra_validation. None when that app is not installed."""
    try:
        fn = frappe.get_attr("vcl_kra_validation.api.validate_cuin")
    except Exception:
        return None
    try:
        return fn(invoice_no=cuin)
    except Exception as e:
        return {"valid": False, "error": str(e)}


def _date_check(doc, kra=None):
    """The date rule for a local invoice. `kra` is a validate_cuin answer already in hand, if any."""
    if doc.get("custom_kra_cuin_exempt"):
        return {**rules.bill_date_review(None, doc.bill_date), "text": "Date not checked: the supplier is CUIN-exempt."}
    cuin = (doc.bill_no or "").strip()
    if kra is None and cuin:
        kra = _kra_lookup(cuin)
    kra_date = (kra or {}).get("inv_date") if (kra or {}).get("valid") else None
    return rules.bill_date_review(kra_date, doc.bill_date)


@frappe.whitelist()
def kra_check(name):
    """KRA's answer for this invoice's CUIN and the server's verdict on the bill date, in one call.

    {kra: validate_cuin's answer or None, date: bill_date_review}. An import has no CUIN check
    (its customs entry is not on the iTax invoice checker): date.state is "unverified".
    """
    _assert_purchasing_role()
    doc = frappe.get_doc(PI, name)
    doc.check_permission("read")
    if rules.is_import(doc.get("custom_purchase_invoice_type")):
        return {"kra": None, "date": {**rules.bill_date_review(None, None), "text": "An import is dated on its KRA customs entry."}}
    cuin = (doc.bill_no or "").strip()
    kra = _kra_lookup(cuin) if cuin and not doc.get("custom_kra_cuin_exempt") else None
    return {"kra": kra, "date": _date_check(doc, kra if kra is not None else {})}


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
    ship_row, ship_count = _shipment_for(doc) if imp else (None, 0)
    pos = _linked_pos(doc)
    proforma = _from_po(doc, "order_confirmation_no") or doc.get("custom_proforma_invoice_number_importation_link") \
        or (ship_row or {}).get("proforma_no")
    file_number, file_src = rules.file_no(_from_po(doc, "custom_file_number"), doc.get("custom_file_number"), proforma)
    # Days free: the invoice's own figure, else the shipment's (def-pc1027-08). 0 is "not entered", not "no free days".
    days_free, days_src = flt(doc.get("custom_number_of_days_free")), "this invoice"
    if not days_free and ship_row and flt(ship_row.get("free_days")):
        days_free, days_src = flt(ship_row.get("free_days")), "the shipment " + ship_row["name"]
    containers = [c.container_reference for c in doc.get("custom_container_details_table") or []]
    return {
        "name": doc.name, "docstatus": doc.docstatus, "status": doc.status,
        "supplier": doc.supplier, "supplier_name": doc.supplier_name or doc.supplier,
        "currency": cur, "conversion_rate": rate, "company": doc.company,
        "invoice_type": itype, "is_import": imp, "type_follows_po": bool(derived),
        "bill_no": doc.bill_no,
        "supplier_ref_no": doc.get("custom_supplier_reference_no"),
        "dates": {"bill_date": rules.iso(doc.bill_date), "kra_entry_date": rules.iso(doc.get("custom_kra_entry_date")),
                  "posting_date": rules.iso(doc.posting_date), "due_date": rules.iso(doc.due_date),
                  "clearance_date": rules.iso(doc.get("clearance_date"))},
        "kra_entry_number": doc.get("custom_kra_import_number"),
        # Filled from the linked PO and not edited on the invoice.
        "proforma_no": proforma,
        "file_no": file_number, "file_no_source": file_src,
        "purchase_orders": pos,
        "update_stock": int(doc.update_stock or 0),
        "shipment": {
            "bill_of_lading_no": doc.get("custom_bill_of_lading_number"),
            "shipping_line": doc.get("custom_shipping_line"),
            "expected_delivery_to_port": rules.iso(doc.get("custom_expected_date_of_delivery_to_port")),
            "days_free": days_free, "days_free_source": days_src if days_free else None,
            "acd_code": doc.get("custom_acd_reference_code"),
            "export_received": 1 if doc.get("custom_export_entry_received") else 0,
            "record": _shipment_page(ship_row, ship_count)["record"], "records_on_order": ship_count,
            "containers": [{"name": c.name, "container_reference": c.container_reference,
                            "packaging": c.description_of_packaging_eg_51_reels_or_515_packages,
                            "gross_weight": flt(c.gross_weight), "net_weight": flt(c.net_weight),
                            "container_size": c.container_size} for c in doc.get("custom_container_details_table") or []],
        },
        "lines": [{"name": i.name, "item_code": i.item_code, "item_name": i.item_name or i.item_code,
                   "qty": flt(i.qty), "uom": i.uom, "rate": flt(i.rate), "amount": flt(i.amount),
                   "warehouse": i.warehouse, "expense_account": i.expense_account,
                   "purchase_order": i.purchase_order, "po_detail": i.get("po_detail")} for i in doc.items],
        "net_total": flt(doc.net_total), "base_net_total": flt(doc.base_net_total),
        # ERPNext's own figure (grand - net), so it agrees with the grand total even when a row is
        # for valuation only; the old signed sum of the rows did not (def-pc1027-06).
        "taxes": taxes, "total_tax_kes": flt(doc.base_grand_total) - flt(doc.base_net_total),
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
        "qbo_memo_standard": rules.qbo_memo(doc.name, doc.bill_no, pos, proforma, file_number,
                                            doc.get("custom_bill_of_lading_number"), containers, cur, doc.net_total,
                                            doc.conversion_rate, doc.bill_date or doc.posting_date),
        "attachments": _attachments(doc.name),
        "requirements": {"missing": missing, "ready": not missing},
        "receive_from": _receive_from(doc),
        "receipt_check": _receipt_check(doc),
        "invoice_types": INVOICE_TYPES,
        "can": {
            "edit": draft,
            "submit": draft and not missing,
            "submit_here": True,
            "pay": doc.docstatus == 1 and outstanding > 0.005,
        },
        "desk_url": f"/app/purchase-invoice/{doc.name}",
    }


# --- the Import Shipment an import invoice belongs to (vcl_imports) ------------------

SHIP = "Import Shipment"
# Voyage facts the shipment record owns (vcl_imports: "the shipment owns them and the PI is never
# consulted again"). The last four arrive with the vcl_imports migrate of 27/10/2026; until then they
# are simply not read or offered.
SHIP_FIELDS = ("vessel", "voyage_no", "bl_date", "idf_number", "port_of_loading",
               "packing_list_gross_kg", "packing_list_net_kg", "packing_list_packages")
SHIP_READ = ("name", "bl_number", "purchase_order", "stage", "free_days", "proforma_no") + SHIP_FIELDS


def _ship_fields():
    if not frappe.db.exists("DocType", SHIP):
        return None
    meta = frappe.get_meta(SHIP)
    return [f for f in SHIP_READ if f == "name" or meta.has_field(f)]


def _shipment_for(doc):
    """(shipment row or None, how many shipments the order has). Never raises: tracking is optional."""
    try:
        fields = _ship_fields()
        pos = _linked_pos(doc)
        if not fields or not (pos or doc.get("custom_bill_of_lading_number")):
            return None, 0
        rows = frappe.get_all(SHIP, filters={"purchase_order": ["in", pos]}, fields=fields,
                              order_by="name asc", limit_page_length=50) if pos else []
        if doc.get("custom_bill_of_lading_number") and not any(
                rules.norm_ref(r.get("bl_number")) == rules.norm_ref(doc.get("custom_bill_of_lading_number")) for r in rows):
            rows = rows + frappe.get_all(SHIP, filters={"bl_number": ["is", "set"]}, fields=fields,
                                         limit_page_length=0)
        hit = rules.pick_shipment(rows, doc.get("custom_bill_of_lading_number"), pos)
        return hit, len([r for r in rows if r.get("purchase_order") in pos])
    except Exception:
        frappe.log_error(title="vcl_procurement: shipment lookup failed", message=frappe.get_traceback())
        return None, 0


def _shipment_page(row, count):
    if not row:
        return {"record": None, "count": count}
    rec = {k: (rules.iso(v) if k == "bl_date" else v) for k, v in row.items()}
    rec["editable"] = [f for f in SHIP_FIELDS if f in row]
    rec["href"] = "/compass/imports?ship=" + row["name"]
    return {"record": rec, "count": count}


def _save_shipment_record(doc, fields):
    """Write the voyage facts typed on the invoice to its shipment, through vcl_imports' own whitelisted
    update (its roles and its list of editable fields). Returns notes, never raises."""
    if not fields:
        return []
    row, count = _shipment_for(doc)
    if not row:
        return ["The vessel, BL date, IDF and packing-list figures were not saved: this invoice has no shipment record"
                + (f" (its order has {count}; enter the BL number so the right one is found)." if count > 1 else ".")]
    allowed = set(_ship_fields() or [])
    want = {k: (v if v not in ("",) else None) for k, v in fields.items() if k in SHIP_FIELDS and k in allowed}
    changed = {k: v for k, v in want.items() if str(row.get(k) or "") != str(v or "")}
    if not changed:
        return []
    frappe.db.savepoint("pi_shipment_record")
    try:
        from vcl_imports.api.shipments import update_shipment
        update_shipment(row["name"], changed)
        return []
    except Exception as e:
        # The invoice save stands; only the shipment's half is undone.
        frappe.db.rollback(save_point="pi_shipment_record")
        frappe.clear_messages()
        return [f"The invoice was saved, but shipment {row['name']} was not: {getattr(e, 'message', None) or e}"]


@frappe.whitelist(methods=["POST"])
def pi_set_ship(name, fields):
    """The two shipment ticks that stay editable after submit: the ACD reference code and the export
    entry. Replaces the screen's direct REST write. Returns the two saved values."""
    _assert_purchasing_role()
    fields = frappe.parse_json(fields) or {}
    doc = frappe.get_doc(PI, name)
    doc.check_permission("write")
    if doc.docstatus == 2:
        frappe.throw(_("{0} is cancelled.").format(name))
    if "custom_acd_reference_code" in fields:
        doc.custom_acd_reference_code = (str(fields["custom_acd_reference_code"] or "").strip() or None)
    if "custom_export_entry_received" in fields:
        doc.custom_export_entry_received = 1 if fields["custom_export_entry_received"] else 0
    if "custom_supplier_reference_no" in fields:
        doc.custom_supplier_reference_no = (str(fields["custom_supplier_reference_no"] or "").strip() or None)
    doc.save()
    return {"custom_acd_reference_code": doc.get("custom_acd_reference_code"),
            "custom_export_entry_received": doc.get("custom_export_entry_received"),
            "custom_supplier_reference_no": doc.get("custom_supplier_reference_no")}


@frappe.whitelist()
def tax_preview(name, payload):
    """The taxes and totals a save WOULD give, worked out by ERPNext itself on an unsaved copy.

    The screen used to run its own copy of the tax chain and on imports it disagreed with the saved
    figures (def-pc1027-06). Now the preview is ERPNext's calculate_taxes_and_totals on the same
    lines, rate and typed taxes `pi_save` would write - nothing is saved.
    """
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    doc = frappe.get_doc(PI, name)
    doc.check_permission("read")
    if doc.docstatus != 0:
        frappe.throw(_("{0} is not a draft.").format(name))
    data = {}
    if flt(payload.get("conversion_rate")) > 0:
        data["conversion_rate"] = flt(payload["conversion_rate"])
    data.update(_lines_and_taxes(doc, payload, data.get("conversion_rate")))
    doc.update(data)
    doc.calculate_taxes_and_totals()
    rate = flt(doc.conversion_rate) or 1
    return {
        "net_total": flt(doc.net_total), "base_net_total": flt(doc.base_net_total),
        "taxes": [{"name": t.name, "idx": t.idx, "tax_amount": flt(t.tax_amount),
                   "kes": flt(t.base_tax_amount) if t.base_tax_amount is not None else rules.doc_to_kes(t.tax_amount, rate, doc.currency)}
                  for t in doc.get("taxes") or []],
        "total_tax_kes": flt(doc.base_grand_total) - flt(doc.base_net_total),
        "grand_total": flt(doc.grand_total), "base_grand_total": flt(doc.base_grand_total),
    }


# --- writing ------------------------------------------------------------------

def _lines_and_taxes(doc, payload, conversion_rate=None):
    """{items?, taxes?} for an edit of a DRAFT: quantities and rates, lines removed, taxes typed in KES.

    Shared by `pi_save` and `tax_preview`, so the preview is worked out on exactly what a save would
    write. `remove_lines` drops invoice rows the supplier did not bill (a PO line the invoice does
    not carry, def-mvqebvoi); at least one line has to stay.
    """
    out = {}
    remove = {n for n in (payload.get("remove_lines") or []) if n}
    if "lines" in payload or remove:
        unknown = remove - {i.name for i in doc.items}
        if unknown:
            frappe.throw(_("Not a line of {0}: {1}").format(doc.name, ", ".join(sorted(unknown))))
        if remove and len(remove) >= len(doc.items):
            frappe.throw(_("An invoice needs at least one line. Cancel or delete the draft instead."))
        want = {ln["name"]: ln for ln in payload.get("lines") or [] if ln.get("name")}
        rows = []
        for i in doc.items:
            if i.name in remove:
                continue
            row = i.as_dict()
            if i.name in want:
                row["qty"] = flt(want[i.name].get("qty"))
                rate_ = flt(want[i.name].get("rate"))
                row["rate"] = rate_
                # `rate` is the only price field: keep ERPNext from re-deriving it (see po_price_guard).
                row.update(price_list_rate=rate_, base_price_list_rate=rate_, margin_type="", rate_with_margin=0,
                           base_rate_with_margin=0, margin_rate_or_amount=0, discount_percentage=0, discount_amount=0)
            rows.append(row)
        for n, row in enumerate(rows, 1):
            row["idx"] = n
        out["items"] = rows

    tx = payload.get("taxes") or {}
    if tx.get("mode") == "actual":
        rate = flt(conversion_rate or doc.conversion_rate) or 1
        kes_by_name = {r["name"]: r.get("kes") for r in tx.get("rows") or [] if r.get("name")}
        rows = []
        for t in doc.get("taxes") or []:
            row = t.as_dict()
            row["charge_type"] = "Actual"
            row["row_id"] = None
            if t.name in kes_by_name:
                row["tax_amount"] = rules.kes_to_doc(kes_by_name[t.name], rate, doc.currency)
            rows.append(row)
        out["taxes"] = rows
    return out

@frappe.whitelist()
def pi_save(name, payload):
    """Save an edit to a DRAFT purchase invoice. Returns {page, notes}.

    payload keys (all optional): bill_no, bill_date, custom_purchase_invoice_type,
    custom_kra_import_number, custom_kra_entry_date, posting_date (non-import only),
    conversion_rate, update_stock, remarks, shipment fields, containers[], lines
    [{name, qty, rate}], remove_lines [name], taxes {mode: "actual"|"calculated", rows: [{name, kes}]},
    schedule {terms_template, tax_days_after_landing}, custom_acd_reference_code,
    custom_export_entry_received, shipment_record {vessel, bl_date, ...} (written to the Import Shipment).
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
    if "custom_export_entry_received" in payload:
        data["custom_export_entry_received"] = 1 if payload["custom_export_entry_received"] else 0
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

    data.update(_lines_and_taxes(doc, payload, data.get("conversion_rate")))
    doc.update(data)

    sched = payload.get("schedule")
    sched_set = False
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
            sched_set = True
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
            sched_set = True

    if not sched_set and doc.get("payment_schedule"):
        # The amounts due follow the invoice (def-mvqeayxy): after a line or tax edit an import's
        # net and taxes rows are put back to the new net and taxes, a single row to the new total.
        doc.calculate_taxes_and_totals()
        total = flt(doc.rounded_total) or flt(doc.grand_total)
        rows = rules.refit_schedule([r.as_dict() for r in doc.payment_schedule], doc.net_total, total, rules.is_import(itype))
        if rows:
            for r, new in zip(doc.payment_schedule, rows):
                r.invoice_portion = new["invoice_portion"]
                r.payment_amount = new["payment_amount"]

    doc.save()
    notes += _save_shipment_record(doc, payload.get("shipment_record"))
    return {"page": pi_page(name), "notes": notes}


@frappe.whitelist()
def schedule_preview(name, payload):
    """The import payment schedule as it WOULD be, without saving anything.

    Keeps the calculation on the server (rules.import_schedule) so the screen never
    holds a second copy of it. payload: {terms_template, tax_days_after_landing,
    bill_date, expected_delivery, taxes_kes?, lines?, remove_lines?, taxes?, conversion_rate?}. Anything
    omitted is read from the invoice; the edit keys (as pi_save takes them) are applied to an unsaved copy
    so the amounts due follow lines and taxes as typed. `taxes_kes` is the older way to pass typed taxes.
    """
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    doc = frappe.get_doc(PI, name)
    doc.check_permission("read")
    edit = {k: payload[k] for k in ("lines", "remove_lines", "taxes", "conversion_rate") if k in payload}
    if edit and doc.docstatus == 0:
        # The amounts due follow the edit as typed (def-mvqeayxy), not the last save: same apply as pi_save.
        data = {"conversion_rate": flt(edit["conversion_rate"])} if flt(edit.get("conversion_rate")) > 0 else {}
        data.update(_lines_and_taxes(doc, edit, data.get("conversion_rate")))
        doc.update(data)
        doc.calculate_taxes_and_totals()
        payload.pop("taxes_kes", None)      # the edit's own taxes are in the totals now
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
            "idx": i.idx, "po_detail": i.name, "item_code": i.item_code, "item_name": i.item_name, "uom": i.uom,
            "description": i.description,
            "qty": flt(i.qty), "received_qty": flt(i.received_qty), "rate": flt(i.rate), "left": left[i.idx],
            # Quantities as well as money, so the workspace can show ordered / received / billed / left (CR-006).
            "billed_qty": rules.billed_qty(i.billed_amt, i.rate, i.qty),
            "left_qty": max(0.0, round(flt(i.qty) - rules.billed_qty(i.billed_amt, i.rate, i.qty), 6)),
        } for i in doc.items if i.idx in left],
        "left_total": round(sum(left.values()), 2),
        "terms": _bill_terms(doc),
        "drafts": _draft_bills(po),
    }


def _bill_terms(po_doc):
    """Payment terms for a new bill: the supplier's default, else the PO's, with the choices."""
    dflt = frappe.db.get_value("Supplier", po_doc.supplier, "payment_terms")
    sel, src = (dflt, "supplier default") if dflt else (po_doc.get("payment_terms_template"), "from the PO") if po_doc.get("payment_terms_template") else (None, None)
    return {"selected": sel, "source": src,
            "options": frappe.get_all("Payment Terms Template", pluck="name", order_by="name asc", limit_page_length=200)}


def _draft_bills(po):
    """Draft invoices already billing this order: [{name, bill_no}]. A second draft is usually a mistake."""
    names = sorted({r.parent for r in frappe.get_all("Purchase Invoice Item", filters={"purchase_order": po, "docstatus": 0},
                                                      fields=["parent"], limit_page_length=50)})
    return [{"name": n, "bill_no": frappe.db.get_value(PI, n, "bill_no")} for n in names]


def _shape_bill(pi, payload):
    """Apply the workspace's choices to an unsaved invoice mapped from the PO (CR-006).

    payload: lines [{po_detail, qty, rate, include}], posting_date, supplier_ref, update_stock.
    Lines not included are dropped; at least one must stay. Quantities above what is left to bill are refused.
    """
    want = {ln.get("po_detail"): ln for ln in payload.get("lines") or [] if ln.get("po_detail")}
    keep = []
    for i in pi.items:
        ln = want.get(i.get("po_detail"))
        if ln is not None and not ln.get("include", True):
            continue
        if ln is not None:
            q, r = flt(ln.get("qty")), flt(ln.get("rate"))
            if q <= 0:
                frappe.throw(_("Quantity must be more than zero ({0}). Untick the line to leave it off this bill.").format(i.item_name or i.item_code))
            if q - flt(i.qty) > 1e-6:
                frappe.throw(_("{0}: {1} is more than the {2} left to bill on the order.").format(i.item_name or i.item_code, q, flt(i.qty)))
            if r < 0:
                frappe.throw(_("Rate cannot be negative ({0}).").format(i.item_name or i.item_code))
            i.qty = q
            i.rate = r
            i.update({"price_list_rate": r, "base_price_list_rate": r, "margin_type": "", "rate_with_margin": 0,
                      "base_rate_with_margin": 0, "margin_rate_or_amount": 0, "discount_percentage": 0, "discount_amount": 0})
        keep.append(i)
    if not keep:
        frappe.throw(_("An invoice needs at least one line. Tick a line to bill."))
    pi.set("items", keep)
    for n, i in enumerate(pi.items, 1):
        i.idx = n
    if (payload.get("supplier_ref") or "").strip():
        pi.custom_supplier_reference_no = payload["supplier_ref"].strip()
    if "update_stock" in payload:
        pi.update_stock = 1 if payload["update_stock"] else 0
    if payload.get("posting_date") and not rules.is_import(pi.get("custom_purchase_invoice_type")):
        pi.posting_date = rules.iso(payload["posting_date"])
        pi.set_posting_time = 0 if pi.posting_date == rules.iso(nowdate()) else 1
    return pi


@frappe.whitelist()
def bill_from_po(po, bill_date=None, bill_no=None):
    """A DRAFT purchase invoice for the unbilled balance of a purchase order.

    The supplier's invoice date is required: it sets the due date, and (for an
    import) it is one of the three things the KRA gate asks for. The number is
    optional and can be added on the invoice page.
    """
    _assert_purchasing_role()
    pi = _mapped_bill(po, bill_date, bill_no)
    pi.insert()
    return {"name": pi.name, "docstatus": pi.docstatus, "po": po,
            "invoice_type": pi.get("custom_purchase_invoice_type"),
            "conversion_rate": flt(pi.conversion_rate) or 1}


def _mapped_bill(po, bill_date, bill_no=None):
    """An UNSAVED draft invoice for the unbilled balance of `po`, dated and typed as bill_from_po always did."""
    if not bill_date:
        frappe.throw(_("Enter the supplier invoice date."))
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice
    pi = make_purchase_invoice(po)
    pi.bill_date = bill_date
    # The mapped schedule counts from today; the supplier's terms count from the invoice date.
    rows = pi.get("payment_schedule") or []
    dues, last = rules.rebase_due_dates(
        [{"due_date_based_on": r.get("due_date_based_on"), "credit_days": r.get("credit_days"),
          "credit_months": r.get("credit_months")} for r in rows], bill_date)
    if dues:
        for r, d in zip(rows, dues):
            if d:
                r.due_date = d
        if last:
            pi.due_date = last
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
    return pi


@frappe.whitelist()
def bill_totals(po, payload):
    """What the bill WOULD come to, worked out by ERPNext on an unsaved copy: net, VAT, gross and the due date (CR-006).
    payload: bill_date, terms, and the workspace's lines / posting date. Nothing is saved."""
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    frappe.get_doc("Purchase Order", po).check_permission("read")
    pi = _shape_bill(_mapped_bill(po, payload.get("bill_date") or nowdate()), payload)
    pi.calculate_taxes_and_totals()
    total = flt(pi.rounded_total) or flt(pi.grand_total)
    due, due_note = None, None
    if not rules.is_import(pi.get("custom_purchase_invoice_type")):
        try:
            term, term_name = _term_row(payload.get("terms"))
            rows, missing = rules.local_schedule(total, payload.get("bill_date"), term, term_name)
            due = rows[0]["due_date"] if rows else None
            due_note = None if rows else "Needs the " + ", ".join(missing) + "."
        except ValueError as e:
            due_note = str(e)
    else:
        due_note = "An import's amounts due are set on the invoice page (supplier terms and the taxes row)."
    return {"currency": pi.currency, "net_total": flt(pi.net_total), "base_net_total": flt(pi.base_net_total),
            "taxes": [{"description": t.description, "kes": flt(t.base_tax_amount)} for t in pi.get("taxes") or []],
            "total_tax_kes": flt(pi.base_grand_total) - flt(pi.base_net_total),
            "grand_total": flt(pi.grand_total), "base_grand_total": flt(pi.base_grand_total),
            "rounded_total": flt(pi.rounded_total), "due_date": rules.iso(due), "due_note": due_note,
            "lines": len(pi.items)}


@frappe.whitelist(methods=["POST"])
def bill_save(po, payload):
    """Create the draft bill from the workspace in one go (CR-006). Returns {name, existing, page, notes}.

    A draft already billing this order under the same supplier invoice no. is returned instead of a second one,
    so a double click or a retry after a timeout cannot duplicate the bill.
    """
    _assert_purchasing_role()
    payload = frappe.parse_json(payload) or {}
    bill_no = (payload.get("bill_no") or "").strip()
    if bill_no:
        for d in _draft_bills(po):
            if (d.get("bill_no") or "").strip() == bill_no:
                return {"name": d["name"], "existing": True, "page": pi_page(d["name"]), "notes": []}
    pi = _shape_bill(_mapped_bill(po, payload.get("bill_date"), bill_no), payload)
    pi.insert()
    notes = []
    if payload.get("terms") and not rules.is_import(pi.get("custom_purchase_invoice_type")):
        r = pi_save(pi.name, {"schedule": {"terms_template": payload["terms"]}})
        notes = r.get("notes") or []
    return {"name": pi.name, "existing": False, "page": pi_page(pi.name), "notes": notes}


# --- cancelling (CR-011) --------------------------------------------------------

def _cancel_state(doc):
    payments = [h["name"] for h in _payments(doc.name) if not h.get("draft")]
    receipts = sorted({r.parent for r in frappe.get_all("Purchase Receipt Item", filters={"purchase_invoice": doc.name, "docstatus": 1},
                                                         fields=["parent"], limit_page_length=50)})
    q = _qbo(doc)
    row = q.get("queue") or {}
    bill_id = q.get("bill_id") or row.get("qbo_bill_id")
    check = rules.cancel_check(doc.docstatus, payments, receipts, row.get("docstatus"), row.get("push_status"), bill_id)
    drafts = [h["name"] for h in _payments(doc.name) if h.get("draft")]
    if drafts:
        check["warnings"].append(f"Draft payment {', '.join(drafts)} will no longer settle anything: delete it after cancelling.")
    return check, {"payments": payments, "receipts": receipts, "qbo_bill_id": bill_id, "queue": row.get("name"),
                   "push_status": row.get("push_status")}


@frappe.whitelist()
def cancel_review(name):
    """CR-011: what cancelling this invoice would do, and what stops it. Nothing is changed."""
    _assert_purchasing_role()
    doc = frappe.get_doc(PI, name)
    doc.check_permission("read")
    check, st = _cancel_state(doc)
    return {**check, **st, "name": doc.name, "supplier": doc.supplier_name or doc.supplier, "currency": doc.currency,
            "grand_total": flt(doc.grand_total), "status": doc.status, "purchase_orders": _linked_pos(doc),
            "stock": bool(int(doc.update_stock or 0)),
            "stock_lines": [{"item": i.item_name or i.item_code, "qty": flt(i.qty), "uom": i.uom, "warehouse": i.warehouse}
                            for i in doc.items] if int(doc.update_stock or 0) else [],
            "can_cancel": bool(frappe.has_permission(PI, "cancel", doc=doc)) and not check["blockers"]}


@frappe.whitelist(methods=["POST"])
def pi_cancel(name, reason, qbo_ack=0):
    """CR-011: cancel a submitted invoice from Compass. Needs a reason, ERPNext's cancel permission, no blocker,
    and, when the bill is already in QuickBooks, `qbo_ack` (the person will void or correct it there).

    The reason is recorded as a comment on the invoice (who and when come with it). ERPNext's own cancel reverses
    the ledger and any stock, and its checks (negative stock, linked documents) still apply; its refusal is the error.
    The QuickBooks queue row is cancelled by the existing on_cancel hook. A repeat on a cancelled invoice changes
    nothing and returns the page.
    """
    _assert_purchasing_role()
    doc = frappe.get_doc(PI, name)
    if doc.docstatus == 2:
        return {"page": pi_page(name), "already": True}
    doc.check_permission("cancel")
    why = (reason or "").strip()
    if len(why) < 5:
        frappe.throw(_("Say why it is being cancelled (a few words)."))
    check, st = _cancel_state(doc)
    if check["blockers"]:
        frappe.throw(_("Not cancelled: {0}").format(" ".join(check["blockers"])))
    if check["needs_qbo_ack"] and not int(qbo_ack or 0):
        frappe.throw(_("QuickBooks bill {0} is not voided by this. Tick that you will void or correct it in QuickBooks.").format(st["qbo_bill_id"]))
    doc.add_comment("Comment", _("Cancelled in Compass. Reason: {0}{1}").format(
        why, _(" QuickBooks bill {0} to be voided or corrected in QuickBooks.").format(st["qbo_bill_id"]) if st["qbo_bill_id"] else ""))
    doc.cancel()
    return {"page": pi_page(name), "already": False}


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
