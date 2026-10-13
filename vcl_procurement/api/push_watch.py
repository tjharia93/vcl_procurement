"""Tell people when a QuickBooks push has been waiting too long.

The push runs on the on-prem box. If the box, the funnel or n8n is down, the queue row waits "Pending";
the box sweeps and pushes it on its own once it is back (sweep_queue.py), so nothing is lost. This job runs
on Frappe Cloud, so it works while the box is down, and emails once per row so the delay is seen.
"""

from datetime import datetime, timedelta

import frappe

QUEUE = "QBO Bill Push Queue"
WAIT_MINUTES = 30
REALERT_SECONDS = 12 * 3600
DEFAULT_RECIPIENTS = ["purchasing@vimit.com", "tanuj.haria@vimit.com"]


def stuck_rows(rows, now, minutes=WAIT_MINUTES):
    """Submitted queue rows still Pending after `minutes` (pure, for testing)."""
    cutoff = now - timedelta(minutes=minutes)
    out = []
    for r in rows:
        if int(r.get("docstatus") or 0) != 1 or (r.get("push_status") or "").strip() != "Pending":
            continue
        m = r.get("modified")
        m = m if isinstance(m, datetime) else datetime.strptime(str(m)[:19], "%Y-%m-%d %H:%M:%S")
        if m <= cutoff:
            out.append(r)
    return out


def _cache():
    return frappe.cache() if callable(frappe.cache) else frappe.cache


def alert_stuck_push_rows():
    """Scheduled every 15 minutes. One email lists rows not yet alerted (each row is re-alerted after 12 h)."""
    rows = frappe.get_all(QUEUE, filters={"docstatus": 1, "push_status": "Pending"},
                          fields=["name", "pi", "supplier", "modified", "qbo_txn_type"], limit_page_length=0)
    stuck = stuck_rows(rows, frappe.utils.now_datetime())
    c = _cache()
    fresh = [r for r in stuck if not c.get_value("qbo_stuck_alert:" + r["name"])]
    if not fresh:
        return 0
    lines = "".join("<li>%s: invoice %s, %s, waiting since %s</li>" % (
        frappe.utils.escape_html(r["name"]), frappe.utils.escape_html(r.get("pi") or ""),
        frappe.utils.escape_html(r.get("supplier") or ""), frappe.utils.format_datetime(r["modified"])) for r in fresh)
    frappe.sendmail(
        recipients=frappe.conf.get("qbo_push_alert_recipients") or DEFAULT_RECIPIENTS,
        subject="QuickBooks push waiting: %d invoice%s" % (len(fresh), "" if len(fresh) == 1 else "s"),
        message=("<p>These invoices were submitted and have not reached QuickBooks for over %d minutes. "
                 "The push runner on the box may be down. The box pushes them by itself when it is back, so nothing is lost.</p><ul>%s</ul>"
                 % (WAIT_MINUTES, lines)))
    for r in fresh:
        c.set_value("qbo_stuck_alert:" + r["name"], 1, expires_in_sec=REALERT_SECONDS)
    return len(fresh)
