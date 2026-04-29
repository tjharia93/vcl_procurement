"""ToDo sidebar integration.

Per memory `feedback_frappe_assign_to.md`: use `frappe.desk.form.assign_to.add` so the
parent doc's _assign field is populated and the sidebar pill shows up. Direct ToDo
inserts leave _assign empty and don't surface in the sidebar.

Items needing the assignee's action get a ToDo here. Items that have been resolved
(pushed, approved, cancelled) get their open ToDo closed. Net effect: the assignee's
sidebar always equals the list of things they need to do — no more, no less.
"""

from __future__ import annotations

import frappe
from frappe.desk.form.assign_to import add as assign_to_add


def _assignee() -> str | None:
    user = frappe.db.get_single_value("QBO Sync Settings", "assignee_user")
    return user or None


def _existing_open_todo(user: str, doctype: str, name: str) -> str | None:
    return frappe.db.get_value(
        "ToDo",
        {
            "allocated_to": user,
            "reference_type": doctype,
            "reference_name": name,
            "status": "Open",
        },
        "name",
    )


def _ensure_assignment(user: str, doctype: str, name: str, description: str) -> None:
    if _existing_open_todo(user, doctype, name):
        return
    try:
        assign_to_add({
            "assign_to": [user],
            "doctype": doctype,
            "name": name,
            "description": description,
        })
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"vcl_procurement: ToDo assignment failed for {doctype} {name}")


def _close_open_todo(user: str, doctype: str, name: str) -> None:
    todo_name = _existing_open_todo(user, doctype, name)
    if not todo_name:
        return
    try:
        frappe.db.set_value("ToDo", todo_name, "status", "Closed", update_modified=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"vcl_procurement: ToDo close failed for ToDo {todo_name}")


def assign_queue_row(queue_doc) -> None:
    """Called from QBO Bill Push Queue after_insert + on_update.

    Decides whether the queue row needs the assignee's attention right now,
    and either opens or closes a ToDo accordingly.
    """
    user = _assignee()
    if not user:
        return

    needs_action = False
    description = ""

    if queue_doc.qbo_bill_id:
        needs_action = False
    elif queue_doc.category in ("ALREADY_SYNCED", "CANCELLED", "DRIFT"):
        needs_action = False
    elif queue_doc.category == "BLOCKED":
        needs_action = True
        description = (
            f"Fix QBO blocker: {queue_doc.pi} — {queue_doc.block_reason or 'see queue row'}"
        )
    elif queue_doc.category in ("NEW", "UPDATE") and not queue_doc.approved:
        needs_action = True
        prefix = "Approve QBO push" if queue_doc.category == "NEW" else "Approve QBO update"
        description = (
            f"{prefix}: {queue_doc.pi} — {queue_doc.supplier or '(no supplier)'} — "
            f"bill {queue_doc.bill_no or '(no bill_no)'} — "
            f"{queue_doc.currency or ''} {queue_doc.total_amt or 0}"
        )
    else:
        needs_action = False

    if needs_action:
        _ensure_assignment(user, queue_doc.doctype, queue_doc.name, description)
    else:
        _close_open_todo(user, queue_doc.doctype, queue_doc.name)


def close_queue_todo(queue_name: str) -> None:
    """Called by mark_pushed once the QBO push succeeds — closes the open ToDo."""
    user = _assignee()
    if not user:
        return
    _close_open_todo(user, "QBO Bill Push Queue", queue_name)


def assign_unapproved_map_row(doctype: str, name: str, primary_value: str) -> None:
    """Called when the runner auto-creates an unapproved map row."""
    user = _assignee()
    if not user:
        return
    description = f"Approve QBO mapping: {doctype} — {primary_value}"
    _ensure_assignment(user, doctype, name, description)


def close_map_todo(doctype: str, name: str) -> None:
    """Called from each map doctype's before_save when approved flips 0→1."""
    user = _assignee()
    if not user:
        return
    _close_open_todo(user, doctype, name)
