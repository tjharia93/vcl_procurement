"""Import shipment money rules, in plain Python: the KRA customs entry, landed cost, the
container deposit and the receiving lock.

Nothing here imports frappe (see `rules.py` for why). `import_costs.py` reads and writes ERPNext.

Decided by Tanuj, October and November 2026:

  * The supplier's import invoice stays a DRAFT until the KRA customs entry is in. It is then
    booked at the KRA customs entry exchange rate and submitted.
  * KRA charges are posted by one Journal Entry, not in the invoice's taxes table. Import VAT goes
    to the input VAT account and never into stock. Duty, IDF, RDL and the MSS levy go to Expenses
    Included In Valuation and from there into the goods by a Landed Cost Voucher.
  * CFS, clearing agent and shipping line charges are capitalised the same way, spread by quantity.
    A refundable container deposit is an asset and is never a cost.
  * What is paid to the supplier later, at the bank's rate, does not change the stock value.
"""

import re

from vcl_procurement.api.rules import RuleError, iso, norm_ref

# The Import Shipment cost types (vcl_imports) each levy belongs to. Order is the order on screen.
LEVIES = (("duty", "Import Duty"), ("idf", "IDF"), ("rdl", "RDL"), ("mss", "MSS Levy"))
VAT = ("vat", "VAT")
LEVY_KEYS = tuple(k for k, _ in LEVIES)
# A typed customs rate this far from the invoice's own rate is nearly always a slipped digit.
RATE_TOLERANCE = 0.15

OFF, DRAFT, POST = "off", "draft", "post"


def mode(switch, allow=None, name=None):
    """What the site lets the customs entry and landed cost screens do for one invoice.

    `switch` is site config `vcl_import_customs_entry`: absent or 0 = off, "draft" = drafts only,
    1 = post. In draft mode an invoice named in `vcl_import_post_for` may post, so one shipment can
    be run for real while everything else stays a draft.
    """
    s = str(switch if switch is not None else "").strip().lower()
    if s in ("1", "true", "post", "on"):
        return POST
    if s == "draft":
        return POST if name and name in set(allow or []) else DRAFT
    return OFF


def amount(v):
    """A typed amount: commas, spaces and a leading currency are accepted. '' and None are 0."""
    if v is None or isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        s = re.sub(r"(?i)^\s*(kes|ksh|kshs|usd|eur)\.?\s*", "", str(v)).replace(",", "").replace(" ", "")
        if s == "":
            return 0.0
        try:
            n = float(s)
        except ValueError:
            raise RuleError(f"'{v}' is not an amount.")
    if n != n or n in (float("inf"), float("-inf")):
        raise RuleError(f"'{v}' is not an amount.")
    if n < 0:
        raise RuleError("An amount cannot be negative.")
    return round(n, 2)


def clean_entry(payload, currency):
    """The customs entry as typed -> one clean dict. Raises RuleError naming what is wrong."""
    p = payload or {}
    out = {"entry_no": re.sub(r"\s+", "", str(p.get("entry_no") or "")).upper(),
           "entry_date": iso(p.get("entry_date")) if p.get("entry_date") else None,
           "paid_from": (str(p.get("paid_from") or "").strip() or None)}
    rate = p.get("rate")
    try:
        out["rate"] = round(float(str(rate).replace(",", "").strip()), 6) if rate not in (None, "") else 0.0
    except ValueError:
        raise RuleError(f"'{rate}' is not an exchange rate.")
    if currency == "KES":
        out["rate"] = 1.0
    for key in LEVY_KEYS + (VAT[0],):
        out[key] = amount(p.get(key))
    return out


def entry_missing(entry, containers):
    """What the customs entry still needs before it can be saved, in words. [] = ready."""
    missing = []
    if not entry.get("entry_no"):
        missing.append("KRA customs entry number")
    if not entry.get("entry_date"):
        missing.append("KRA customs entry date")
    if not entry.get("rate") or entry["rate"] <= 0:
        missing.append("customs exchange rate")
    if not [c for c in containers or [] if str(c or "").strip()]:
        missing.append("container number on the invoice")
    if not sum(entry.get(k) or 0 for k in LEVY_KEYS) and not entry.get("vat"):
        missing.append("at least one KRA amount")
    if not entry.get("paid_from"):
        missing.append("the account KRA was paid from")
    return missing


def rate_warning(customs_rate, invoice_rate, tolerance=RATE_TOLERANCE):
    """'' or why the typed customs rate looks wrong against the rate the draft invoice holds."""
    c, i = float(customs_rate or 0), float(invoice_rate or 0)
    if c <= 0 or i <= 0:
        return ""
    if abs(c - i) / i > tolerance:
        return (f"The customs rate {c:g} is more than {int(tolerance * 100)}% away from the invoice's "
                f"rate {i:g}. Check it against the customs entry, then confirm.")
    return ""


def journal_rows(entry, accounts, reference):
    """The balanced Journal Entry rows for one customs entry.

    `accounts` = {"vat": input VAT account, "valuation": Expenses Included In Valuation}.
    Every row's remark carries the customs entry number, so the VAT report can show it.
    """
    for key, words in (("vat", "the import VAT account (site config vcl_import_vat_account)"),
                       ("valuation", "the company's Expenses Included In Valuation account")):
        need = entry.get("vat") if key == "vat" else sum(entry.get(k) or 0 for k in LEVY_KEYS)
        if need and not accounts.get(key):
            raise RuleError(f"Not posted: {words} is not set.")
    rows = []
    if entry.get("vat"):
        rows.append({"account": accounts["vat"], "debit": entry["vat"], "credit": 0.0,
                     "remark": f"Import VAT · {reference}"})
    for key, label in LEVIES:
        if entry.get(key):
            rows.append({"account": accounts["valuation"], "debit": entry[key], "credit": 0.0,
                         "remark": f"{label} · {reference}"})
    total = round(sum(r["debit"] for r in rows), 2)
    if total <= 0:
        raise RuleError("Enter at least one KRA amount.")
    rows.append({"account": entry["paid_from"], "debit": 0.0, "credit": total,
                 "remark": f"KRA customs entry · {reference}"})
    return rows


def reference(entry_no, bill_no, containers):
    """One line naming the entry, the supplier invoice and the containers. Goes on every row."""
    parts = [f"Entry {entry_no}"]
    if bill_no:
        parts.append(f"Inv {bill_no}")
    boxes = [str(c).strip().upper() for c in containers or [] if str(c or "").strip()]
    if boxes:
        parts.append(", ".join(boxes))
    return " · ".join(parts)[:140]


def schedule_without_taxes(rows, grand):
    """An import's two-row schedule (net, taxes) once the taxes are on the customs entry instead:
    one row, the whole invoice, on the supplier's own due date. None when there is nothing to change."""
    if not rows or len(rows) != 2:
        return None
    if abs(float(rows[1].get("payment_amount") or 0)) > 0.005:
        return None
    return [{**rows[0], "invoice_portion": 100.0, "payment_amount": round(float(grand or 0), 2)}]


# --- the receiving lock ---------------------------------------------------------

def receiving_refusal(site_mode, order_type, invoices):
    """'' or why goods on this order cannot be received yet.

    Only when the site posts customs entries (mode POST), and only for an import order. `invoices`
    are the order's import invoices: [{name, docstatus, customs_entry (submitted JE or None)}].
    Stock must come in at the customs rate, so the invoice has to be submitted on its entry first.
    """
    if site_mode != POST or str(order_type or "").strip().lower() != "import":
        return ""
    live = [i for i in invoices or [] if int(i.get("docstatus") or 0) < 2]
    if not live:
        return ("Receiving is locked: this import order has no supplier invoice yet. "
                "Enter the invoice and post its KRA customs entry first.")
    ready = [i for i in live if int(i.get("docstatus") or 0) == 1 and i.get("customs_entry")]
    if ready:
        return ""
    drafts = [i["name"] for i in live if int(i.get("docstatus") or 0) == 0]
    if drafts:
        return (f"Receiving is locked: {', '.join(drafts)} is still a draft. Post its KRA customs entry, "
                "which books it at the customs rate, then receive.")
    return ("Receiving is locked: " + ", ".join(i["name"] for i in live) +
            " has no posted KRA customs entry.")


def gate_check(container, seal, known_containers, known_seals=None):
    """The check at the gate: does this truck carry a container on the customs entry?

    Returns {ok, why}. A seal is compared only when the invoice records seals for its containers.
    """
    box = norm_ref(container)
    known = {norm_ref(c) for c in known_containers or [] if norm_ref(c)}
    if not box:
        return {"ok": False, "why": "Enter the container number painted on the container."}
    if box not in known:
        return {"ok": False, "why": f"Container {str(container).strip().upper()} is not on this shipment. Do not unload."}
    want = {norm_ref(k): norm_ref(v) for k, v in (known_seals or {}).items() if norm_ref(v)}
    if box in want:
        if not norm_ref(seal):
            return {"ok": False, "why": "Enter the seal number before the seal is broken."}
        if norm_ref(seal) != want[box]:
            return {"ok": False, "why": "The seal number does not match the shipping documents. Do not unload."}
    return {"ok": True, "why": ""}


# --- landed cost ----------------------------------------------------------------

def landed_pending(sources, booked, floor=1.0):
    """What a Landed Cost Voucher should still carry: sources less what vouchers already hold.

    `sources` = [{account, description, amount, source}] in KES: the customs entry's levies and the
    cost lines of submitted charge invoices. `booked` = {account: KES already on vouchers that are
    not cancelled}. Earlier sources are treated as booked first, so a second voucher carries only
    what arrived after the first. Amounts under `floor` shillings are rounding, not a cost.
    """
    left = {a: round(float(v or 0), 2) for a, v in (booked or {}).items()}
    out = []
    for s in sources or []:
        amt = round(float(s.get("amount") or 0), 2)
        if amt <= 0:
            continue
        used = min(amt, left.get(s["account"], 0.0))
        left[s["account"]] = round(left.get(s["account"], 0.0) - used, 2)
        rest = round(amt - used, 2)
        if rest >= floor:
            out.append({**s, "amount": rest})
    return out


def landed_merge(rows):
    """One voucher row per account, descriptions joined, in first-seen order."""
    merged, order = {}, []
    for r in rows or []:
        a = r["account"]
        if a not in merged:
            merged[a] = {"account": a, "amount": 0.0, "descriptions": []}
            order.append(a)
        merged[a]["amount"] = round(merged[a]["amount"] + float(r["amount"]), 2)
        d = str(r.get("description") or "").strip()
        if d and d not in merged[a]["descriptions"]:
            merged[a]["descriptions"].append(d)
    return [{"account": a, "amount": merged[a]["amount"],
             "description": "; ".join(merged[a]["descriptions"])[:140] or "Import charges"} for a in order]


def per_kg(total, kg):
    kg = float(kg or 0)
    return round(float(total or 0) / kg, 2) if kg > 0 else None


# --- the container deposit and the close ------------------------------------------

def deposit_recovery(balance, refund, deductions):
    """Check a recovery against the deposit still held. Returns (refund, deductions)."""
    refund, deductions = amount(refund), amount(deductions)
    balance = round(float(balance or 0), 2)
    if refund + deductions <= 0:
        raise RuleError("Enter what came back, or what was deducted.")
    if refund + deductions > balance + 0.005:
        raise RuleError(f"That is more than the deposit still held ({balance:,.2f}).")
    return refund, deductions


def close_state(supplier_outstanding, charges_open, deposit_balance, landed_pending_total, received):
    """What still keeps an import shipment open, in words, in the order to deal with it."""
    waiting = []
    if not received:
        waiting.append("Goods not received")
    if round(float(landed_pending_total or 0), 2) >= 1:
        waiting.append(f"KES {float(landed_pending_total):,.2f} of charges not yet in the stock value")
    if int(charges_open or 0):
        waiting.append(f"{int(charges_open)} charge invoice(s) unpaid or in draft")
    if round(float(deposit_balance or 0), 2) >= 1:
        waiting.append(f"Container deposit of {float(deposit_balance):,.2f} not recovered")
    if round(float(supplier_outstanding or 0), 2) >= 0.01:
        waiting.append("Supplier not fully paid")
    return {"can_close": not waiting, "waiting": waiting}
