"""An import shipment's money, after the supplier invoice is entered: the KRA customs entry,
landed cost into the stock value, the container deposit and what still keeps the shipment open.

The rules are in `import_rules.py` (pure, tested). This file reads and writes ERPNext.

**Switched off until the site says otherwise.** Site config `vcl_import_customs_entry`:
absent or 0 = off (every write refuses, the page says so); "draft" = documents are made as
drafts and nothing posts; 1 = posts. In "draft" mode, invoices named in `vcl_import_post_for`
(a list) may post, so one shipment can be run end to end while the rest stay drafts.

Other site config:
  vcl_import_vat_account        input VAT account for import VAT. No default: posting refuses without it.
  vcl_import_paid_from_accounts extra accounts KRA can be paid from (bank and cash accounts are always offered)
  vcl_import_deposit_accounts   the container deposit asset accounts (defaults below)

**One call order, enforced here.** `customs_save` makes a DRAFT Journal Entry and writes the
customs rate, entry number and entry date onto the DRAFT invoice. `customs_post` submits the
Journal Entry and then the invoice, in one transaction. Receiving against an import order is
refused until that has happened (`receiving_lock`), so stock always arrives at the customs rate.
"""

import frappe
from frappe import _
from frappe.utils import flt

from vcl_procurement.api import import_rules as R, rules
from vcl_procurement.api.purchasing import PURCHASING_ROLES

PI, JE, PR, LCV = "Purchase Invoice", "Journal Entry", "Purchase Receipt", "Landed Cost Voucher"
FINANCE_ROLES = {"Accounts Manager", "Accounts User", "System Manager"}
KIND_CUSTOMS, KIND_DEPOSIT = "Customs entry", "Deposit recovery"
DEPOSIT_ACCOUNTS = ["1652 - CONTAINER DEPOSIT - VCL", "1100.700.100 - Container Deposit - VCL"]


# --- gates and settings ---------------------------------------------------------

def _roles():
    return set(frappe.get_roles())


def _assert_read():
    if not (_roles() & (PURCHASING_ROLES | FINANCE_ROLES)):
        frappe.throw(_("Import costs are for Purchasing and Finance."), frappe.PermissionError)


def _assert_finance():
    if not (_roles() & FINANCE_ROLES):
        frappe.throw(_("Only Finance can post import costs."), frappe.PermissionError)


def _conf(key):
    return (getattr(frappe, "conf", None) or {}).get(key)


def _mode(name=None):
    return R.mode(_conf("vcl_import_customs_entry"), _conf("vcl_import_post_for") or [], name)


def _assert_on(name, need=R.DRAFT):
    m = _mode(name)
    if m == R.OFF:
        frappe.throw(_("Import customs entries are switched off on this site."))
    if need == R.POST and m != R.POST:
        frappe.throw(_("This site keeps import cost documents as drafts for now. Nothing was posted."))
    return m


def _ready():
    """The Custom Fields arrive with a migrate; say so plainly on a site that has not run it."""
    meta = frappe.get_meta(JE)
    if not (meta.has_field("custom_import_invoice") and meta.has_field("custom_import_entry_kind")):
        frappe.throw(_("Deploy vcl_procurement and run migrate to install the import cost fields."))


def _import_invoice(name, perm="read"):
    doc = frappe.get_doc(PI, name)
    doc.check_permission(perm)
    if doc.get("is_return") or not rules.is_import(doc.get("custom_purchase_invoice_type")):
        frappe.throw(_("{0} is not an importation invoice.").format(name))
    return doc


def _containers(doc):
    return [c.container_reference for c in doc.get("custom_container_details_table") or [] if c.container_reference]


def _company(doc, field):
    return frappe.get_cached_value("Company", doc.company, field)


def _accounts(doc):
    return {"vat": _conf("vcl_import_vat_account") or None,
            "valuation": _company(doc, "expenses_included_in_valuation") or None}


def _paid_from_options(company):
    rows = frappe.get_all("Account", filters={"company": company, "is_group": 0, "disabled": 0,
                                               "account_type": ["in", ["Bank", "Cash"]],
                                               "account_currency": rules.COMPANY_CURRENCY},
                          fields=["name"], order_by="name asc", limit_page_length=0)
    names = [r.name for r in rows]
    for extra in _conf("vcl_import_paid_from_accounts") or []:
        if extra not in names and frappe.db.get_value("Account", extra, "company") == company:
            names.append(extra)
    return names


def _entries(name, kind, docstatus=("<", 2)):
    return frappe.get_all(JE, filters={"custom_import_invoice": name, "custom_import_entry_kind": kind,
                                       "docstatus": list(docstatus)},
                          fields=["name", "docstatus", "posting_date", "total_debit", "custom_customs_entry_no"],
                          order_by="creation asc", limit_page_length=0)


def _entry_rows(je_name):
    return frappe.get_all("Journal Entry Account", filters={"parent": je_name},
                          fields=["account", "debit", "credit", "user_remark"], parent_doctype=JE,
                          order_by="idx asc", limit_page_length=0)


def _je_row(row, cost_center):
    return {"account": row["account"], "debit_in_account_currency": row["debit"],
            "credit_in_account_currency": row["credit"], "user_remark": row["remark"], "cost_center": cost_center}


def _shipment_note(doc, entry):
    """Tell the Import Shipment its customs entry number and date, through vcl_imports' own methods.
    Never raises: the shipment record is tracking, the books are what must be right."""
    try:
        from vcl_procurement.api.pi import _shipment_for
        row, _count = _shipment_for(doc)
        if not row:
            return []
        frappe.db.savepoint("import_costs_shipment")
        from vcl_imports.api.shipments import set_milestone, update_shipment
        update_shipment(row["name"], {"customs_entry_no": entry["entry_no"]})
        set_milestone(row["name"], "date_customs_entry", entry["entry_date"])
        return []
    except Exception as e:
        try:
            frappe.db.rollback(save_point="import_costs_shipment")
        except Exception:
            pass
        frappe.clear_messages()
        return [f"The shipment record was not updated: {getattr(e, 'message', None) or e}"]


# --- the customs entry ------------------------------------------------------------

def _customs_state(doc):
    rows = _entries(doc.name, KIND_CUSTOMS)
    je = rows[-1] if rows else None
    out = {"journal_entry": je.name if je else None, "posted": bool(je and je.docstatus == 1),
           "entry_no": doc.get("custom_kra_import_number"), "entry_date": rules.iso(doc.get("custom_kra_entry_date")),
           "rate": flt(doc.get("custom_customs_exchange_rate")) or None, "invoice_rate": flt(doc.conversion_rate) or 1,
           "amounts": {}, "paid_from": None, "total": flt(je.total_debit) if je else 0}
    if je:
        accounts = _accounts(doc)
        for r in _entry_rows(je.name):
            if flt(r.credit):
                out["paid_from"] = r.account
                continue
            label = str(r.user_remark or "").split(" · ")[0]
            key = R.VAT[0] if r.account == accounts["vat"] and label == "Import VAT" else next(
                (k for k, words in R.LEVIES if words == label), None)
            if key:
                out["amounts"][key] = flt(out["amounts"].get(key)) + flt(r.debit)
    return out


@frappe.whitelist(methods=["POST"])
def customs_save(pi, payload, confirm_rate=0):
    """Save the KRA customs entry as a DRAFT Journal Entry and put the customs rate, entry number
    and entry date on the DRAFT invoice. Nothing posts. Returns {page, notes}."""
    _assert_finance()
    _assert_on(pi)
    _ready()
    doc = _import_invoice(pi, "write")
    if doc.docstatus != 0:
        frappe.throw(_("{0} is not a draft. A customs entry is saved before the invoice is submitted.").format(pi))
    if flt(doc.get("total_taxes_and_charges")):
        frappe.throw(_("{0} still carries taxes in its own taxes table. Set them to zero first: "
                       "the customs entry posts the KRA charges, and they would be booked twice.").format(pi))
    try:
        entry = R.clean_entry(frappe.parse_json(payload) or {}, doc.currency)
        containers = _containers(doc)
        missing = R.entry_missing(entry, containers)
        if missing:
            raise R.RuleError("Not saved. Still needed: " + ", ".join(missing) + ".")
        warn = R.rate_warning(entry["rate"], doc.conversion_rate)
        if warn and not int(confirm_rate or 0):
            raise R.RuleError(warn)
        ref = R.reference(entry["entry_no"], doc.bill_no, containers)
        rows = R.journal_rows(entry, _accounts(doc), ref)
    except R.RuleError as e:
        frappe.throw(str(e))
    if entry["paid_from"] not in _paid_from_options(doc.company):
        frappe.throw(_("Choose the bank or cash account KRA was paid from."))
    clash = frappe.get_all(JE, filters={"custom_customs_entry_no": entry["entry_no"], "docstatus": ["<", 2],
                                        "custom_import_invoice": ["!=", pi]}, pluck="name", limit_page_length=1)
    if clash:
        frappe.throw(_("Customs entry {0} is already on {1}.").format(entry["entry_no"], clash[0]))

    existing = _entries(pi, KIND_CUSTOMS)
    if any(e.docstatus == 1 for e in existing):
        frappe.throw(_("The customs entry for {0} is already posted.").format(pi))
    je = frappe.get_doc(JE, existing[-1].name) if existing else frappe.new_doc(JE)
    cost_center = _company(doc, "cost_center")
    je.update({"voucher_type": "Journal Entry", "company": doc.company, "posting_date": entry["entry_date"],
               "cheque_no": entry["entry_no"], "cheque_date": entry["entry_date"], "user_remark": "KRA customs entry · " + ref,
               "custom_import_invoice": pi, "custom_import_entry_kind": KIND_CUSTOMS,
               "custom_customs_entry_no": entry["entry_no"]})
    je.set("accounts", [_je_row(r, cost_center) for r in rows])
    je.save()

    doc.update({"conversion_rate": entry["rate"], "custom_customs_exchange_rate": entry["rate"],
                "custom_kra_import_number": entry["entry_no"], "custom_kra_entry_date": entry["entry_date"],
                "posting_date": entry["entry_date"], "set_posting_time": 1, "custom_customs_journal_entry": je.name})
    doc.calculate_taxes_and_totals()
    one_row = R.schedule_without_taxes([r.as_dict() for r in doc.get("payment_schedule") or []],
                                       flt(doc.rounded_total) or flt(doc.grand_total))
    if one_row:
        doc.payment_terms_template = None
        doc.set("payment_schedule", one_row)
    doc.save()
    notes = _shipment_note(doc, entry)
    return {"page": import_page(pi), "notes": notes}


@frappe.whitelist(methods=["POST"])
def customs_post(pi):
    """Submit the customs Journal Entry, then the invoice at the customs rate. One transaction:
    if the invoice refuses to submit, the Journal Entry is taken back to a draft."""
    _assert_finance()
    _assert_on(pi, R.POST)
    _ready()
    doc = _import_invoice(pi, "submit")
    if doc.docstatus != 0:
        frappe.throw(_("{0} is not a draft.").format(pi))
    drafts = [e for e in _entries(pi, KIND_CUSTOMS) if e.docstatus == 0]
    if not drafts:
        frappe.throw(_("Save the customs entry for {0} first.").format(pi))
    if abs(flt(doc.conversion_rate) - flt(doc.get("custom_customs_exchange_rate"))) > 1e-6:
        frappe.throw(_("The invoice's rate was changed after the customs entry was saved. Save the customs entry again."))
    from vcl_procurement.api import pi as pi_api
    missing = pi_api._missing(doc)
    if missing:
        frappe.throw(_("Not posted. Still pending:<br>{0}").format("<br>".join(missing)))
    frappe.db.savepoint("customs_post")
    try:
        je = frappe.get_doc(JE, drafts[-1].name)
        je.check_permission("submit")
        je.submit()
        doc.submit()
    except Exception:
        frappe.db.rollback(save_point="customs_post")
        raise
    if frappe.db.get_value(PI, pi, "docstatus") != 1:
        frappe.throw(_("The server replied, but {0} is still a draft. Nothing has posted.").format(pi))
    return {"page": import_page(pi), "notes": []}


# --- the receiving lock and the gate ------------------------------------------------

def _order_invoices(po):
    names = sorted({r.parent for r in frappe.get_all(
        "Purchase Invoice Item", filters={"purchase_order": po, "docstatus": ["<", 2]}, fields=["parent"],
        parent_doctype=PI, limit_page_length=0)})
    if not names:
        return []
    heads = frappe.get_all(PI, filters={"name": ["in", names], "is_return": 0,
                                        "custom_purchase_invoice_type": rules.IMPORT},
                           fields=["name", "docstatus"], limit_page_length=0)
    posted = set(frappe.get_all(JE, filters={"custom_import_invoice": ["in", [h.name for h in heads]],
                                             "custom_import_entry_kind": KIND_CUSTOMS, "docstatus": 1},
                                pluck="custom_import_invoice", limit_page_length=0)) if heads else set()
    return [{"name": h.name, "docstatus": h.docstatus, "customs_entry": h.name in posted} for h in heads]


def receiving_lock(po):
    """'' or why this order's goods cannot be received yet. Called by every receive path."""
    site = _mode()
    if site != R.POST:
        return ""
    order_type = frappe.db.get_value("Purchase Order", po, "custom_order_type")
    if str(order_type or "").strip().lower() != "import":
        return ""
    return R.receiving_refusal(site, order_type, _order_invoices(po))


def assert_can_receive(po):
    why = receiving_lock(po)
    if why:
        frappe.throw(why, title=_("Receiving locked"))


def lock_before_submit(doc, method=None):
    """`before_submit` on Purchase Receipt, so the Desk is held to the same lock as the screens."""
    if doc.get("is_return"):
        return
    for po in sorted({r.purchase_order for r in doc.items if r.get("purchase_order")}):
        assert_can_receive(po)


@frappe.whitelist()
def gate_check(pi, container, seal=None):
    """At the gate, before the seal is broken: is this container on the shipment? {ok, why}."""
    _assert_read()
    doc = _import_invoice(pi)
    return R.gate_check(container, seal, _containers(doc))


# --- landed cost ------------------------------------------------------------------

def _receipts(doc):
    details = [i.get("po_detail") for i in doc.items if i.get("po_detail")]
    if not details:
        return []
    rows = frappe.get_all("Purchase Receipt Item", filters={"purchase_order_item": ["in", details], "docstatus": 1},
                          fields=["parent", "stock_qty", "stock_uom"], parent_doctype=PR, limit_page_length=0)
    out = {}
    for r in rows:
        rec = out.setdefault(r.parent, {"name": r.parent, "kg": 0.0, "lines": 0})
        rec["lines"] += 1
        if str(r.stock_uom or "").lower() == "kg":
            rec["kg"] += flt(r.stock_qty)
    return [out[k] for k in sorted(out)]


def _charges(name):
    meta = frappe.get_meta(PI)
    if not meta.has_field("custom_import_parent"):
        return []
    fields = ["name", "docstatus", "bill_no", "supplier_name", "outstanding_amount", "base_grand_total"]
    if meta.has_field("custom_charge_type"):
        fields.append("custom_charge_type")
    return frappe.get_all(PI, filters={"custom_import_parent": name, "docstatus": ["<", 2]}, fields=fields,
                          order_by="creation asc", limit_page_length=0)


def _deposit_accounts():
    return list(_conf("vcl_import_deposit_accounts") or DEPOSIT_ACCOUNTS)


def _charge_lines(charges):
    submitted = [c.name for c in charges if c.docstatus == 1]
    if not submitted:
        return []
    return frappe.get_all("Purchase Invoice Item", filters={"parent": ["in", submitted]},
                          fields=["parent", "expense_account", "base_net_amount", "item_name"],
                          parent_doctype=PI, order_by="parent asc, idx asc", limit_page_length=0)


def _landed(doc, charges):
    receipts = _receipts(doc)
    accounts = _accounts(doc)
    sources = []
    for e in _entries(doc.name, KIND_CUSTOMS, ("=", 1)):
        for r in _entry_rows(e.name):
            if flt(r.debit) and r.account == accounts["valuation"]:
                sources.append({"account": r.account, "amount": flt(r.debit), "source": e.name,
                                "description": str(r.user_remark or "KRA levy").split(" · ")[0] + " (customs entry)"})
    lines = _charge_lines(charges)
    deposit = set(_deposit_accounts())
    roots = {a.name: a.root_type for a in frappe.get_all(
        "Account", filters={"name": ["in", sorted({l.expense_account for l in lines if l.expense_account})]},
        fields=["name", "root_type"], limit_page_length=0)} if lines else {}
    by_charge = {c.name: c for c in charges}
    for l in lines:
        if l.expense_account in deposit or roots.get(l.expense_account) != "Expense":
            continue
        c = by_charge[l.parent]
        sources.append({"account": l.expense_account, "amount": flt(l.base_net_amount), "source": l.parent,
                        "description": f"{c.get('custom_charge_type') or 'Charge'} {c.bill_no or l.parent}"})
    vouchers, booked = [], {}
    names = [r["name"] for r in receipts]
    if names:
        parents = sorted({r.parent for r in frappe.get_all(
            "Landed Cost Purchase Receipt", filters={"receipt_document": ["in", names], "docstatus": ["<", 2]},
            fields=["parent"], parent_doctype=LCV, limit_page_length=0)})
        for v in parents:
            head = frappe.db.get_value(LCV, v, ["docstatus", "posting_date", "total_taxes_and_charges"], as_dict=True)
            vouchers.append({"name": v, "docstatus": head.docstatus, "date": rules.iso(head.posting_date),
                             "total": flt(head.total_taxes_and_charges)})
            for t in frappe.get_all("Landed Cost Taxes and Charges", filters={"parent": v},
                                    fields=["expense_account", "amount", "base_amount"], parent_doctype=LCV,
                                    limit_page_length=0):
                booked[t.expense_account] = flt(booked.get(t.expense_account)) + (flt(t.base_amount) or flt(t.amount))
    pending = R.landed_merge(R.landed_pending(sources, booked))
    kg = sum(r["kg"] for r in receipts)
    in_stock = sum(v["total"] for v in vouchers if v["docstatus"] == 1)
    return {"receipts": receipts, "kg": kg, "sources": sources, "vouchers": vouchers, "pending": pending,
            "pending_total": round(sum(p["amount"] for p in pending), 2),
            "capitalised": round(in_stock, 2), "capitalised_per_kg": R.per_kg(in_stock, kg),
            "draft": next((v["name"] for v in vouchers if v["docstatus"] == 0), None)}


@frappe.whitelist(methods=["POST"])
def landed_create(pi):
    """Make a DRAFT Landed Cost Voucher for what is not yet in the stock value: the customs levies
    and the submitted charge invoices, spread by quantity over the goods received. Never submits."""
    _assert_finance()
    _assert_on(pi)
    _ready()
    doc = _import_invoice(pi)
    state = _landed(doc, _charges(pi))
    if not state["receipts"]:
        frappe.throw(_("Nothing has been received for {0} yet. Landed cost is added after receiving.").format(pi))
    if state["draft"]:
        frappe.throw(_("{0} is still a draft. Post or delete it before making another.").format(state["draft"]))
    if not state["pending"]:
        frappe.throw(_("Every posted charge for {0} is already in the stock value.").format(pi))
    lcv = frappe.new_doc(LCV)
    lcv.company = doc.company
    lcv.distribute_charges_based_on = "Qty"
    for r in state["receipts"]:
        head = frappe.db.get_value(PR, r["name"], ["supplier", "posting_date", "base_grand_total"], as_dict=True)
        lcv.append("purchase_receipts", {"receipt_document_type": PR, "receipt_document": r["name"],
                                         "supplier": head.supplier, "posting_date": head.posting_date,
                                         "grand_total": head.base_grand_total})
    lcv.get_items_from_purchase_receipts()
    for p in state["pending"]:
        lcv.append("taxes", {"expense_account": p["account"], "description": p["description"], "amount": p["amount"]})
    lcv.insert()
    return {"page": import_page(pi), "voucher": lcv.name, "notes": []}


@frappe.whitelist(methods=["POST"])
def landed_post(pi, voucher):
    """Submit a draft Landed Cost Voucher made here, then re-read it."""
    _assert_finance()
    _assert_on(pi, R.POST)
    doc = _import_invoice(pi)
    state = _landed(doc, _charges(pi))
    if voucher != state["draft"]:
        frappe.throw(_("{0} is not this shipment's draft landed cost voucher.").format(voucher))
    lcv = frappe.get_doc(LCV, voucher)
    lcv.check_permission("submit")
    frappe.db.savepoint("landed_post")
    try:
        lcv.submit()
    except Exception:
        frappe.db.rollback(save_point="landed_post")
        raise
    if frappe.db.get_value(LCV, voucher, "docstatus") != 1:
        frappe.throw(_("The server replied, but {0} is still a draft. Nothing has posted.").format(voucher))
    return {"page": import_page(pi), "voucher": voucher, "notes": []}


# --- the container deposit ----------------------------------------------------------

def _deposit(doc, charges):
    accounts = _deposit_accounts()
    held = {}
    for l in _charge_lines(charges):
        if l.expense_account in accounts:
            held[l.expense_account] = flt(held.get(l.expense_account)) + flt(l.base_net_amount)
    recovered, entries = {}, []
    for e in _entries(doc.name, KIND_DEPOSIT):
        entries.append({"name": e.name, "docstatus": e.docstatus, "date": rules.iso(e.posting_date), "total": flt(e.total_debit)})
        if e.docstatus != 1:
            continue
        for r in _entry_rows(e.name):
            if r.account in accounts and flt(r.credit):
                recovered[r.account] = flt(recovered.get(r.account)) + flt(r.credit)
    currencies = {a.name: a.account_currency for a in frappe.get_all(
        "Account", filters={"name": ["in", sorted(held)]}, fields=["name", "account_currency"],
        limit_page_length=0)} if held else {}
    rows = [{"account": a, "held": round(held[a], 2), "recovered": round(flt(recovered.get(a)), 2),
             "balance": round(held[a] - flt(recovered.get(a)), 2),
             # A foreign-currency deposit account needs a multi-currency entry, which this screen does not make.
             "here": currencies.get(a) in (None, rules.COMPANY_CURRENCY)} for a in sorted(held)]
    return {"rows": rows, "balance": round(sum(r["balance"] for r in rows), 2), "entries": entries}


@frappe.whitelist(methods=["POST"])
def deposit_recover(pi, payload):
    """Record a container deposit coming back: Dr the bank for the refund, Dr an expense account for
    anything the shipping line kept, Cr the deposit. A draft, or posted where the site allows it."""
    _assert_finance()
    m = _assert_on(pi)
    _ready()
    doc = _import_invoice(pi)
    p = frappe.parse_json(payload) or {}
    state = _deposit(doc, _charges(pi))
    row = next((r for r in state["rows"] if r["account"] == p.get("account")), None) or (
        state["rows"][0] if len(state["rows"]) == 1 else None)
    if not row:
        frappe.throw(_("Choose which deposit is being recovered."))
    if not row["here"]:
        frappe.throw(_("{0} is a foreign-currency account. Record this recovery in ERPNext.").format(row["account"]))
    if any(e["docstatus"] == 0 for e in state["entries"]):
        frappe.throw(_("A deposit recovery for {0} is still a draft. Post or delete it first.").format(pi))
    try:
        refund, deductions = R.deposit_recovery(row["balance"], p.get("refund"), p.get("deductions"))
    except R.RuleError as e:
        frappe.throw(str(e))
    date = rules.iso(p.get("date")) if p.get("date") else None
    if not date:
        frappe.throw(_("Choose the date the deposit came back."))
    cost_center = _company(doc, "cost_center")
    ref = R.reference(doc.get("custom_kra_import_number") or "-", doc.bill_no, _containers(doc))
    rows = []
    if refund:
        if p.get("received_into") not in _paid_from_options(doc.company):
            frappe.throw(_("Choose the bank or cash account the refund was received into."))
        rows.append({"account": p["received_into"], "debit": refund, "credit": 0.0, "remark": "Container deposit refunded · " + ref})
    if deductions:
        acct = frappe.db.get_value("Account", p.get("deduction_account"), ["company", "is_group", "root_type"], as_dict=True)
        if not acct or acct.company != doc.company or acct.is_group or acct.root_type != "Expense":
            frappe.throw(_("Choose the expense account for what the shipping line kept."))
        rows.append({"account": p["deduction_account"], "debit": deductions, "credit": 0.0,
                     "remark": "Kept from container deposit · " + ref})
    rows.append({"account": row["account"], "debit": 0.0, "credit": round(refund + deductions, 2),
                 "remark": "Container deposit recovered · " + ref})
    je = frappe.new_doc(JE)
    je.update({"voucher_type": "Journal Entry", "company": doc.company, "posting_date": date,
               "cheque_no": str(p.get("reference") or doc.bill_no or pi)[:140], "cheque_date": date,
               "user_remark": "Container deposit recovery · " + ref,
               "custom_import_invoice": pi, "custom_import_entry_kind": KIND_DEPOSIT})
    je.set("accounts", [_je_row(r, cost_center) for r in rows])
    je.insert()
    notes = []
    if m == R.POST:
        je.check_permission("submit")
        je.submit()
        if abs(row["balance"] - refund - deductions) < 0.005 and len(state["rows"]) == 1:
            notes += _deposit_released(doc)
    else:
        notes.append(f"{je.name} was saved as a draft. Nothing has posted.")
    return {"page": import_page(pi), "journal_entry": je.name, "notes": notes}


def _deposit_released(doc):
    try:
        from vcl_procurement.api.pi import _shipment_for
        row, _count = _shipment_for(doc)
        if not row:
            return []
        frappe.db.savepoint("import_costs_deposit")
        from vcl_imports.api.shipments import set_deposit_released
        set_deposit_released(row["name"], 1)
        return []
    except Exception as e:
        try:
            frappe.db.rollback(save_point="import_costs_deposit")
        except Exception:
            pass
        frappe.clear_messages()
        return [f"The shipment record was not marked deposit released: {getattr(e, 'message', None) or e}"]


# --- the page -------------------------------------------------------------------

@frappe.whitelist()
def import_page(pi):
    """Everything the import costs screen shows for one importation invoice. Read-only."""
    _assert_read()
    doc = _import_invoice(pi)
    m = _mode(pi)
    finance = bool(_roles() & FINANCE_ROLES)
    out = {"name": doc.name, "docstatus": doc.docstatus, "mode": m, "can_post": finance and m == R.POST,
           "can_save": finance and m != R.OFF, "currency": doc.currency, "bill_no": doc.bill_no,
           "supplier": doc.supplier_name or doc.supplier, "containers": _containers(doc),
           "net_total": flt(doc.net_total), "taxes_on_invoice": flt(doc.get("total_taxes_and_charges")),
           "ready": frappe.get_meta(JE).has_field("custom_import_invoice")}
    if not out["ready"]:
        return out
    charges = _charges(pi)
    accounts = _accounts(doc)
    out["accounts"] = {**accounts, "paid_from": _paid_from_options(doc.company) if finance else []}
    out["customs"] = _customs_state(doc)
    out["landed"] = _landed(doc, charges)
    out["deposit"] = _deposit(doc, charges)
    goods = flt(doc.base_net_total)
    out["stock_value"] = {"goods": goods, "capitalised": out["landed"]["capitalised"],
                          "per_kg": R.per_kg(goods + out["landed"]["capitalised"], out["landed"]["kg"])}
    charges_open = len([c for c in charges if c.docstatus == 0 or flt(c.outstanding_amount) > 0.005])
    out["close"] = R.close_state(flt(doc.outstanding_amount) if doc.docstatus == 1 else flt(doc.grand_total),
                                 charges_open, out["deposit"]["balance"], out["landed"]["pending_total"],
                                 bool(out["landed"]["receipts"]))
    return out
