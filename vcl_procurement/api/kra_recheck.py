"""KRA lookups that did not answer in time wait for a daily re-check (Tanuj, 09/11/2026).

A live check on the invoice screen gets KRA_DEADLINE_SECONDS (several short attempts: KRA drops requests, it rarely
answers slowly). If KRA stays silent the invoice is marked Pending, nobody is held up beyond the existing date rule
("unverified": only a Purchase Manager or Accounts Manager may push it), and `run` looks again every morning.
After RECHECK_MAX_TRIES silent passes, or when KRA says it has no such invoice, or the bill date does not fit KRA's
date, the invoice is reported by email (site config `kra_recheck_recipients`, default account@ and tanuj.haria@).
"""
import frappe

from vcl_procurement.api import rules

PI = "Purchase Invoice"
_FIELDS = ("custom_kra_recheck", "custom_kra_recheck_tries")


def _set(name, status, tries):
    frappe.db.set_value(PI, name, {"custom_kra_recheck": status, "custom_kra_recheck_tries": tries}, update_modified=False)


def _comment(name, text):
    try:
        frappe.get_doc(PI, name).add_comment("Info", text)
    except Exception:
        pass   # a comment must never stop the check


def note_live_lookup(doc, kra):
    """After a live lookup on the screen: park the invoice when KRA was silent; settle it when it had been parked and KRA now answered.
    Returns the invoice's re-check status after this lookup, or None when it has none."""
    name = doc.get("name")
    if not name or doc.get("docstatus") == 2:
        return None
    current = doc.get("custom_kra_recheck") or None
    if rules.kra_is_transient(kra):
        if current != "Pending":
            _set(name, "Pending", 0)
            _comment(name, "KRA did not answer within %d seconds; it will be checked again every morning." % rules.KRA_DEADLINE_SECONDS)
        return "Pending"
    if current == "Pending":
        outcome = rules.recheck_outcome(kra, doc.get("bill_date"))
        _set(name, outcome, doc.get("custom_kra_recheck_tries") or 0)
        _comment(name, "KRA re-check: %s." % outcome)
        return outcome
    return current


def run():
    """Scheduled 06:30 daily. Re-checks every Pending invoice; one email lists what needs a person."""
    from vcl_procurement.api import pi

    rows = frappe.get_all(PI, filters={"custom_kra_recheck": "Pending", "docstatus": ["<", 2]},
                          fields=["name", "bill_no", "bill_date", "supplier", "custom_kra_recheck_tries", "custom_kra_cuin_exempt"],
                          limit_page_length=0)
    done = {"Verified": 0, "Pending": 0, "Mismatch": 0, "Failed": 0}
    report = []
    for r in rows:
        cuin = (r.get("bill_no") or "").strip()
        if r.get("custom_kra_cuin_exempt") or not cuin:
            _set(r["name"], "Verified" if r.get("custom_kra_cuin_exempt") else "Failed", r.get("custom_kra_recheck_tries") or 0)
            continue
        kra = pi._kra_lookup(cuin, deadline=30)   # nobody is waiting on the screen: allow a longer budget
        outcome = rules.recheck_outcome(kra, r.get("bill_date"))
        status, tries, needs_person = rules.next_recheck_state(outcome, int(r.get("custom_kra_recheck_tries") or 0))
        _set(r["name"], status, tries)
        done[status] = done.get(status, 0) + 1
        _comment(r["name"], "KRA daily re-check: %s%s." % (status, " (pass %d of %d)" % (tries, rules.RECHECK_MAX_TRIES) if status == "Pending" else ""))
        if needs_person:
            report.append((r, status, (kra or {}).get("error") or ""))
    if report:
        _email(report)
    return {**done, "reported": len(report), "checked": len(rows)}


def _email(report):
    esc = frappe.utils.escape_html
    lines = "".join("<li>%s, %s, supplier invoice %s: %s%s</li>" % (
        esc(r["name"]), esc(r.get("supplier") or ""), esc(r.get("bill_no") or ""), esc(status), (" (%s)" % esc(err)) if err else "")
        for r, status, err in report)
    frappe.sendmail(
        recipients=frappe.conf.get("kra_recheck_recipients") or rules.KRA_RECHECK_RECIPIENTS,
        subject="KRA check needs a person: %d invoice%s" % (len(report), "" if len(report) == 1 else "s"),
        message=("<p>These Purchase Invoices could not be confirmed against KRA by the daily re-check. "
                 "Failed: KRA did not answer after %d mornings, or has no such invoice. Mismatch: KRA answered and the bill date needs review.</p><ul>%s</ul>"
                 % (rules.RECHECK_MAX_TRIES, lines)))
