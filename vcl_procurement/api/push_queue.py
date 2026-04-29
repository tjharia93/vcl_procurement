"""Push Queue + Drift Log helpers for the runner.

Idempotency: queue rows are keyed per `pi` (one row per Purchase Invoice
across the lifetime of the queue, not per staging cycle). The `run_id`
field is retained for audit but is no longer part of the unique key.
This matches the event-driven staging model where each PI has exactly
one queue row, kept in sync with each amendment / re-stage.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import now_datetime

from vcl_procurement.api import todos


_QUEUE_DOCTYPE = "QBO Bill Push Queue"
_DRIFT_DOCTYPE = "QBO Drift Log"

_RUNNER_OWNED_FIELDS = {
    "approved",
    "approved_by",
    "approved_at",
    "qbo_bill_id",
    "qbo_sync_token",
    "pushed_at",
    "attempts",
    "last_attempt_at",
    "error_message",
}


@frappe.whitelist(methods=["POST"])
def upsert_queue_row(payload: Any) -> dict:
    """Idempotent per `pi`. One queue row per PI across the lifetime of the queue."""
    parsed = payload if isinstance(payload, dict) else json.loads(payload)

    pi_name = parsed.get("pi")
    if not pi_name:
        frappe.throw("`pi` is required.")

    existing = frappe.get_all(
        _QUEUE_DOCTYPE,
        filters={"pi": pi_name},
        pluck="name",
        limit=1,
    )

    if existing:
        doc = frappe.get_doc(_QUEUE_DOCTYPE, existing[0])
        for field, value in parsed.items():
            if field in _RUNNER_OWNED_FIELDS:
                continue
            doc.set(field, value)
        doc.save(ignore_permissions=True)
        action = "updated"
    else:
        doc = frappe.new_doc(_QUEUE_DOCTYPE)
        for field, value in parsed.items():
            doc.set(field, value)
        doc.insert(ignore_permissions=True)
        action = "created"

    frappe.db.commit()
    return {"name": doc.name, "category": doc.category, "action": action}


@frappe.whitelist(methods=["GET"])
def get_approved_pending(run_id: str | None = None) -> list[dict]:
    filters: dict = {"approved": 1, "qbo_bill_id": ["in", ["", None]], "category": ["in", ["NEW", "UPDATE"]]}
    if run_id:
        filters["run_id"] = run_id
    rows = frappe.get_all(
        _QUEUE_DOCTYPE,
        filters=filters,
        fields=["name", "pi", "supplier", "bill_no", "category", "payload_hash", "payload_json", "run_id"],
        order_by="creation asc",
    )
    return rows


@frappe.whitelist(methods=["POST"])
def mark_pushed(name: str, qbo_bill_id: str, qbo_sync_token: str, last_updated_time: str | None = None) -> dict:
    if not name or not qbo_bill_id:
        frappe.throw("`name` and `qbo_bill_id` are required.")
    pushed_at = now_datetime()
    frappe.db.set_value(_QUEUE_DOCTYPE, name, {
        "qbo_bill_id": qbo_bill_id,
        "qbo_sync_token": qbo_sync_token,
        "pushed_at": pushed_at,
        "last_attempt_at": pushed_at,
        "attempts": (frappe.db.get_value(_QUEUE_DOCTYPE, name, "attempts") or 0) + 1,
        "error_message": "",
        "category": "ALREADY_SYNCED",
    }, update_modified=True)

    pi_name = frappe.db.get_value(_QUEUE_DOCTYPE, name, "pi")
    if pi_name:
        frappe.db.set_value("Purchase Invoice", pi_name, {
            "custom_qbo_bill_id": qbo_bill_id,
            "custom_qbo_synced_at": pushed_at,
            "custom_qbo_sync_status": "Pushed",
        }, update_modified=False)

    todos.close_queue_todo(name)
    frappe.db.commit()
    return {"name": name, "qbo_bill_id": qbo_bill_id, "pushed_at": str(pushed_at)}


@frappe.whitelist(methods=["POST"])
def mark_push_failed(name: str, error_message: str) -> dict:
    if not name:
        frappe.throw("`name` is required.")
    last_attempt_at = now_datetime()
    frappe.db.set_value(_QUEUE_DOCTYPE, name, {
        "last_attempt_at": last_attempt_at,
        "attempts": (frappe.db.get_value(_QUEUE_DOCTYPE, name, "attempts") or 0) + 1,
        "error_message": (error_message or "")[:65000],
    }, update_modified=True)
    frappe.db.commit()
    return {"name": name, "last_attempt_at": str(last_attempt_at)}


@frappe.whitelist(methods=["POST"])
def record_drift(payload: Any) -> dict:
    parsed = payload if isinstance(payload, dict) else json.loads(payload)
    pi = parsed.get("pi")
    qbo_bill_id = parsed.get("qbo_bill_id")
    drift_kind = parsed.get("drift_kind")
    diff_json = parsed.get("diff_json")

    if not (pi and qbo_bill_id and drift_kind):
        frappe.throw("`pi`, `qbo_bill_id`, `drift_kind` are required.")

    doc = frappe.new_doc(_DRIFT_DOCTYPE)
    doc.pi = pi
    doc.qbo_bill_id = qbo_bill_id
    doc.drift_kind = drift_kind
    doc.detected_at = now_datetime()
    if diff_json:
        doc.diff_json = diff_json if isinstance(diff_json, str) else json.dumps(diff_json, ensure_ascii=False)
    doc.insert(ignore_permissions=True)

    frappe.db.set_value("Purchase Invoice", pi, {"custom_qbo_sync_status": "Drift"}, update_modified=False)
    frappe.db.commit()
    return {"name": doc.name}
