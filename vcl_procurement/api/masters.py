"""Bulk upsert endpoints for QBO master cache.

Called by the external runner once per weekly cycle. One call per entity type.
Each call replaces the user's idea of "what's in QBO" for that type:
existing rows are updated by qbo_id, new rows inserted, rows whose qbo_id
no longer appears in `rows` are flagged active=0 (not deleted, so map rows
keep their Link integrity).
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import now_datetime


_DOCTYPE_BY_ENTITY: dict[str, str] = {
    "vendor": "QBO Vendor",
    "account": "QBO Account",
    "item": "QBO Item",
    "tax_code": "QBO Tax Code",
}

_TYPED_FIELDS_BY_ENTITY: dict[str, tuple[str, ...]] = {
    "vendor": ("display_name", "active", "email", "phone", "tax_identifier"),
    "account": ("fully_qualified_name", "account_type", "account_sub_type", "classification", "currency", "active"),
    "item": ("item_name", "type", "expense_account_ref", "expense_account_name", "income_account_ref", "asset_account_ref", "active"),
    "tax_code": ("tax_code_name", "rate_value", "taxable", "active", "description"),
}


@frappe.whitelist(methods=["POST"])
def upsert_qbo_masters(entity_type: str, rows: Any) -> dict:
    if entity_type not in _DOCTYPE_BY_ENTITY:
        frappe.throw(f"Unknown entity_type {entity_type!r}. Expected one of: {list(_DOCTYPE_BY_ENTITY)}")

    parsed_rows = rows if isinstance(rows, list) else json.loads(rows)
    if not isinstance(parsed_rows, list):
        frappe.throw("`rows` must be a list of objects.")

    doctype = _DOCTYPE_BY_ENTITY[entity_type]
    typed_fields = _TYPED_FIELDS_BY_ENTITY[entity_type]
    sync_ts = now_datetime()
    seen_ids: set[str] = set()

    inserted = 0
    updated = 0
    deactivated = 0

    for row in parsed_rows:
        qbo_id = str(row.get("qbo_id") or "").strip()
        if not qbo_id:
            continue
        seen_ids.add(qbo_id)

        if frappe.db.exists(doctype, qbo_id):
            doc = frappe.get_doc(doctype, qbo_id)
            for field in typed_fields:
                if field in row:
                    doc.set(field, row[field])
            doc.raw_json = json.dumps(row.get("raw_json") or row, ensure_ascii=False)
            doc.last_synced_at = sync_ts
            doc.save(ignore_permissions=True)
            updated += 1
        else:
            doc = frappe.new_doc(doctype)
            doc.qbo_id = qbo_id
            for field in typed_fields:
                if field in row:
                    doc.set(field, row[field])
            doc.raw_json = json.dumps(row.get("raw_json") or row, ensure_ascii=False)
            doc.last_synced_at = sync_ts
            doc.insert(ignore_permissions=True)
            inserted += 1

    if seen_ids:
        stale = frappe.get_all(
            doctype,
            filters={"active": 1, "name": ["not in", list(seen_ids)]},
            pluck="name",
        )
        for stale_name in stale:
            frappe.db.set_value(doctype, stale_name, {"active": 0, "last_synced_at": sync_ts}, update_modified=False)
            deactivated += 1

    frappe.db.commit()

    return {
        "entity_type": entity_type,
        "doctype": doctype,
        "received": len(parsed_rows),
        "inserted": inserted,
        "updated": updated,
        "deactivated": deactivated,
        "synced_at": str(sync_ts),
    }
