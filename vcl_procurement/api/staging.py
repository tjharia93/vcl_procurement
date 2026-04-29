"""Event-driven staging: PI submission → QBO Bill Push Queue row.

Wired in `hooks.py` as a doc_event for Purchase Invoice. Runs in <1s,
no QBO calls. Builds the QBO Bill payload from PI data + approved
mapping rows, categorizes, upserts a queue row keyed by `pi`, and
updates the PI's `custom_qbo_sync_status` pill.

A backfill helper exists for the one-time pass over PIs already
submitted before the app was installed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

import frappe
from frappe.utils import getdate, now_datetime

from vcl_procurement.api import resolvers, todos
from vcl_procurement.api.mappings import upsert_unapproved_map_row


_QUEUE_DOCTYPE = "QBO Bill Push Queue"
_PI_DOCTYPE = "Purchase Invoice"
_DOCNUMBER_LIMIT = 21
_PI_STATUS_NOT_SYNCED = "Not Synced"
_PI_STATUS_QUEUED = "Queued"
_PI_STATUS_BLOCKED = "Blocked"
_PI_STATUS_PUSHED = "Pushed"
_PI_STATUS_DRIFT = "Drift"
_PI_STATUS_SKIPPED = "Skipped"


# ---------------------------------------------------------------------------
# Doc event hook wrappers (wired in hooks.py)
# ---------------------------------------------------------------------------


def on_submit_purchase_invoice(doc, method=None) -> None:
    """Catch-all wrapper. Staging failures must NEVER block PI submission."""
    try:
        stage_pi_to_queue(doc)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"vcl_procurement: stage_pi_to_queue failed for {doc.name}",
        )


def on_cancel_purchase_invoice(doc, method=None) -> None:
    try:
        _cancel_queue_row_for_pi(doc.name)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"vcl_procurement: cancel queue row failed for {doc.name}",
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def stage_pi(pi_name: str) -> dict:
    """Manual entry point — restage a single PI by name. Used by the backfill
    helper and by ad-hoc bench/REST calls when a user wants to re-trigger
    staging after fixing a mapping or PI field."""
    if not pi_name:
        frappe.throw("`pi_name` is required.")
    pi_doc = frappe.get_doc(_PI_DOCTYPE, pi_name)
    return stage_pi_to_queue(pi_doc)


@frappe.whitelist(methods=["POST"])
def backfill(start_date: str | None = None, end_date: str | None = None, dry_run: int | bool = 1) -> dict:
    """One-time backfill of submitted Local PIs in the date window.

    Idempotent: re-runs upsert by `pi`, so calling it twice with the same
    window doesn't duplicate queue rows.
    """
    settings = _get_settings()
    start = getdate(start_date) if start_date else getdate(settings.earliest_posting_date)
    end = getdate(end_date) if end_date else getdate(now_datetime())
    is_dry = bool(int(dry_run)) if isinstance(dry_run, (int, str)) else bool(dry_run)

    pi_names = frappe.get_all(
        _PI_DOCTYPE,
        filters={
            "docstatus": 1,
            "posting_date": ["between", [start, end]],
        },
        pluck="name",
        order_by="posting_date asc, name asc",
    )

    summary = {
        "considered": len(pi_names),
        "staged": 0,
        "skipped_import": 0,
        "skipped_pre_cutoff": 0,
        "skipped_other": 0,
        "errored": 0,
        "by_category": {},
        "dry_run": is_dry,
        "window": [str(start), str(end)],
    }

    for pi_name in pi_names:
        try:
            pi_doc = frappe.get_doc(_PI_DOCTYPE, pi_name)
            decision = _evaluate_filters(pi_doc, settings)
            if decision != "PASS":
                if decision == "SKIP_IMPORT":
                    summary["skipped_import"] += 1
                elif decision == "SKIP_PRE_CUTOFF":
                    summary["skipped_pre_cutoff"] += 1
                else:
                    summary["skipped_other"] += 1
                continue

            if is_dry:
                payload, category, _ = _evaluate_pi(pi_doc)
                summary["by_category"][category] = summary["by_category"].get(category, 0) + 1
                summary["staged"] += 1
            else:
                result = stage_pi_to_queue(pi_doc)
                category = result.get("category")
                if category:
                    summary["by_category"][category] = summary["by_category"].get(category, 0) + 1
                if result.get("action") in ("created", "updated"):
                    summary["staged"] += 1
        except Exception:
            summary["errored"] += 1
            frappe.log_error(frappe.get_traceback(), f"vcl_procurement: backfill error on {pi_name}")

    return summary


def stage_pi_to_queue(pi_doc, *, run_id: str | None = None) -> dict:
    """Build payload, categorize, upsert queue row, update PI pill.

    Returns:
        {
            "action": "skipped" | "created" | "updated",
            "reason": str (when skipped),
            "queue_name": str,
            "category": str,
        }
    """
    settings = _get_settings()

    if not settings.staging_enabled:
        _set_pi_status(pi_doc.name, _PI_STATUS_NOT_SYNCED)
        return {"action": "skipped", "reason": "staging_disabled"}

    decision = _evaluate_filters(pi_doc, settings)
    if decision == "SKIP_IMPORT":
        _set_pi_status(pi_doc.name, _PI_STATUS_SKIPPED)
        return {"action": "skipped", "reason": "not_local"}
    if decision == "SKIP_PRE_CUTOFF":
        _set_pi_status(pi_doc.name, _PI_STATUS_SKIPPED)
        return {"action": "skipped", "reason": "before_cutoff"}
    if decision == "SKIP_NOT_SUBMITTED":
        _set_pi_status(pi_doc.name, _PI_STATUS_NOT_SYNCED)
        return {"action": "skipped", "reason": "not_submitted"}

    payload, category, block_reason = _evaluate_pi(pi_doc)
    payload_hash = _hash_payload(payload)
    line_routing = _summarize_routing(payload)
    inherited_qbo_bill_id = _walk_amended_chain_for_qbo_id(pi_doc)

    fields = {
        "pi": pi_doc.name,
        "bill_no": pi_doc.bill_no or None,
        "txn_date": pi_doc.bill_date or pi_doc.posting_date,
        "currency": pi_doc.currency,
        "exchange_rate": pi_doc.conversion_rate or 1.0,
        "total_amt": pi_doc.grand_total,
        "category": category,
        "block_reason": block_reason or None,
        "line_routing_summary": line_routing,
        "payload_hash": payload_hash,
        "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_default),
        "run_id": run_id or now_datetime().strftime("STG-%Y%m%d-%H%M%S"),
    }

    existing_name = frappe.db.get_value(_QUEUE_DOCTYPE, {"pi": pi_doc.name}, "name")

    if existing_name:
        existing = frappe.get_doc(_QUEUE_DOCTYPE, existing_name)
        # Re-categorize against current state, but preserve push results + approval state.
        if existing.qbo_bill_id and payload_hash == existing.payload_hash:
            fields["category"] = "ALREADY_SYNCED"
        elif existing.qbo_bill_id and payload_hash != existing.payload_hash and category != "BLOCKED":
            fields["category"] = "UPDATE"
            existing.approved = 0
            existing.approved_by = None
            existing.approved_at = None
        for key, value in fields.items():
            existing.set(key, value)
        existing.save(ignore_permissions=True)
        action = "updated"
        queue_name = existing.name
        category = existing.category
    else:
        if inherited_qbo_bill_id and category != "BLOCKED":
            fields["category"] = "UPDATE"
            category = "UPDATE"
        new_doc = frappe.new_doc(_QUEUE_DOCTYPE)
        for key, value in fields.items():
            new_doc.set(key, value)
        if inherited_qbo_bill_id:
            new_doc.qbo_bill_id = inherited_qbo_bill_id
        new_doc.insert(ignore_permissions=True)
        action = "created"
        queue_name = new_doc.name

    _set_pi_status_from_category(pi_doc.name, category, has_qbo_id=bool(inherited_qbo_bill_id))

    frappe.db.commit()
    return {"action": action, "queue_name": queue_name, "category": category}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _get_settings():
    return frappe.get_cached_doc("QBO Sync Settings")


def _evaluate_filters(pi_doc, settings) -> str:
    if pi_doc.docstatus != 1:
        return "SKIP_NOT_SUBMITTED"

    cutoff = getdate(settings.earliest_posting_date)
    posting = getdate(pi_doc.posting_date) if pi_doc.posting_date else None
    if not posting or posting < cutoff:
        return "SKIP_PRE_CUTOFF"

    fieldname = (settings.import_or_local_fieldname or "").strip()
    expected = (settings.import_or_local_local_value or "").strip()
    if fieldname:
        actual = pi_doc.get(fieldname)
        if actual is None:
            return "SKIP_IMPORT"
        if str(actual).strip().lower() != expected.lower():
            return "SKIP_IMPORT"

    return "PASS"


def _evaluate_pi(pi_doc) -> tuple[dict, str, str]:
    """Resolves all maps, builds the QBO Bill payload, returns (payload, category, block_reason)."""
    blockers: list[str] = []

    if not (pi_doc.bill_no or "").strip():
        blockers.append("missing bill_no")
    elif len(pi_doc.bill_no) > _DOCNUMBER_LIMIT:
        blockers.append(f"bill_no '{pi_doc.bill_no}' exceeds QBO DocNumber limit ({_DOCNUMBER_LIMIT} chars)")

    if getattr(pi_doc, "is_return", 0):
        blockers.append("is_return=1 — debit notes need manual QBO entry until Phase 4")

    vendor = resolvers.resolve_supplier(pi_doc.supplier)
    if not vendor:
        upsert_unapproved_map_row("QBO Vendor Map", {"supplier": pi_doc.supplier})
        blockers.append(f"no QBO Vendor Map for supplier '{pi_doc.supplier}'")
    elif not vendor["approved"]:
        blockers.append(f"QBO Vendor Map for '{pi_doc.supplier}' is unapproved")

    lines: list[dict] = []
    item_based_count = 0
    account_based_count = 0

    for idx, item in enumerate(pi_doc.items or [], start=1):
        line, kind, line_blockers = _resolve_line(idx, item)
        blockers.extend(line_blockers)
        if line and not line_blockers:
            lines.append(line)
            if kind == "item":
                item_based_count += 1
            elif kind == "account":
                account_based_count += 1

    tax_lines: list[dict] = []
    for idx, tax in enumerate(pi_doc.taxes or [], start=1):
        tax_blockers = _resolve_tax(idx, pi_doc, tax, tax_lines)
        blockers.extend(tax_blockers)

    payload = _build_qbo_bill_payload(pi_doc, vendor, lines, tax_lines, item_based_count, account_based_count)

    if blockers:
        return payload, "BLOCKED", "; ".join(blockers)

    if pi_doc.get("custom_qbo_bill_id"):
        return payload, "UPDATE", ""
    return payload, "NEW", ""


def _resolve_line(idx: int, item) -> tuple[dict | None, str, list[str]]:
    blockers: list[str] = []

    if item.get("item_code"):
        item_map = resolvers.resolve_item(item.item_code)
        if item_map and item_map["approved"]:
            return (
                {
                    "DetailType": "ItemBasedExpenseLineDetail",
                    "Amount": float(item.amount or 0),
                    "Description": item.description or item.item_name or item.item_code,
                    "ItemBasedExpenseLineDetail": {
                        "ItemRef": {"value": item_map["qbo_id"], "name": item_map.get("qbo_name")},
                        "Qty": float(item.qty or 0),
                        "UnitPrice": float(item.rate or 0),
                    },
                },
                "item",
                [],
            )
        if item_map is None:
            upsert_unapproved_map_row("QBO Item Map", {"erp_item_code": item.item_code})

    expense_account = item.get("expense_account")
    if expense_account:
        account_map = resolvers.resolve_account(expense_account)
        if account_map and account_map["approved"]:
            return (
                {
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "Amount": float(item.amount or 0),
                    "Description": item.description or item.item_name or expense_account,
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": account_map["qbo_id"], "name": account_map.get("qbo_name")},
                    },
                },
                "account",
                [],
            )
        if account_map is None:
            upsert_unapproved_map_row("QBO Account Map", {"erp_account": expense_account})
            blockers.append(f"line {idx}: no QBO Account Map for '{expense_account}'")
        else:
            blockers.append(f"line {idx}: QBO Account Map for '{expense_account}' is unapproved")
    else:
        blockers.append(f"line {idx}: no item_code and no expense_account")

    return None, "none", blockers


def _resolve_tax(idx: int, pi_doc, tax, tax_lines: list[dict]) -> list[str]:
    if not (tax.get("account_head") and tax.get("rate")):
        return []
    template = pi_doc.get("taxes_and_charges")
    if not template:
        return [f"tax line {idx}: PI has no taxes_and_charges template — cannot resolve tax map"]
    tax_map = resolvers.resolve_tax(template, tax.account_head, tax.rate)
    if tax_map and tax_map["approved"]:
        tax_lines.append(
            {
                "TaxLineDetail": {
                    "TaxRateRef": {"value": tax_map["qbo_id"], "name": tax_map.get("qbo_name")},
                    "PercentBased": True,
                    "TaxPercent": float(tax.rate or 0),
                },
                "Amount": float(tax.tax_amount or 0),
            }
        )
        return []
    if tax_map is None:
        upsert_unapproved_map_row(
            "QBO Purchase Tax Map",
            {
                "erp_tax_template": template,
                "erp_tax_account_head": tax.account_head,
                "erp_tax_rate": tax.rate,
            },
        )
        return [f"tax line {idx}: no QBO Purchase Tax Map for ({template} | {tax.account_head} | {tax.rate})"]
    return [f"tax line {idx}: QBO Purchase Tax Map for ({template} | {tax.account_head} | {tax.rate}) is unapproved"]


def _build_qbo_bill_payload(
    pi_doc,
    vendor: dict | None,
    lines: list[dict],
    tax_lines: list[dict],
    item_based_count: int,
    account_based_count: int,
) -> dict:
    payload: dict[str, Any] = {
        "DocNumber": pi_doc.bill_no or "",
        "TxnDate": str(pi_doc.bill_date or pi_doc.posting_date or ""),
        "PrivateNote": f"ERPNext PI: {pi_doc.name}",
        "CurrencyRef": {"value": pi_doc.currency or "KES"},
        "ExchangeRate": float(pi_doc.conversion_rate or 1.0),
        "Line": lines,
    }
    if vendor:
        payload["VendorRef"] = {"value": vendor.get("qbo_id"), "name": vendor.get("qbo_name")}
    if tax_lines:
        payload["TxnTaxDetail"] = {"TaxLine": tax_lines}
    payload["_meta"] = {
        "item_based_lines": item_based_count,
        "account_based_lines": account_based_count,
        "erp_pi_name": pi_doc.name,
    }
    return payload


def _summarize_routing(payload: dict) -> str:
    meta = payload.get("_meta", {})
    return f"{meta.get('item_based_lines', 0)} item-based, {meta.get('account_based_lines', 0)} account-based"


def _hash_payload(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "_meta"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, default=_json_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def _walk_amended_chain_for_qbo_id(pi_doc) -> str | None:
    current = getattr(pi_doc, "amended_from", None)
    while current:
        qbo_id = frappe.db.get_value(_PI_DOCTYPE, current, "custom_qbo_bill_id")
        if qbo_id:
            return qbo_id
        current = frappe.db.get_value(_PI_DOCTYPE, current, "amended_from")
    return None


def _cancel_queue_row_for_pi(pi_name: str) -> None:
    queue_name = frappe.db.get_value(_QUEUE_DOCTYPE, {"pi": pi_name}, "name")
    if not queue_name:
        return
    frappe.db.set_value(
        _QUEUE_DOCTYPE,
        queue_name,
        {"category": "CANCELLED", "approved": 0, "approved_by": None, "approved_at": None},
        update_modified=True,
    )
    _set_pi_status(pi_name, _PI_STATUS_NOT_SYNCED)
    todos.close_queue_todo(queue_name)
    frappe.db.commit()


def _set_pi_status(pi_name: str, status: str) -> None:
    try:
        frappe.db.set_value(
            _PI_DOCTYPE,
            pi_name,
            {"custom_qbo_sync_status": status},
            update_modified=False,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"vcl_procurement: PI status update failed for {pi_name}")


def _set_pi_status_from_category(pi_name: str, category: str, *, has_qbo_id: bool) -> None:
    if has_qbo_id and category == "ALREADY_SYNCED":
        status = _PI_STATUS_PUSHED
    elif category == "BLOCKED":
        status = _PI_STATUS_BLOCKED
    elif category == "ALREADY_SYNCED":
        status = _PI_STATUS_PUSHED
    elif category == "DRIFT":
        status = _PI_STATUS_DRIFT
    elif category == "CANCELLED":
        status = _PI_STATUS_NOT_SYNCED
    else:
        status = _PI_STATUS_QUEUED
    _set_pi_status(pi_name, status)
