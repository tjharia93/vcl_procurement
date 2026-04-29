"""Mapping doctype helpers for the runner.

The runner uses these to:
 - auto-create unapproved Map rows with fuzzy suggestions when staging a PI surfaces a new entity
 - report unapproved counts for the weekly summary email
"""

from __future__ import annotations

import frappe

from vcl_procurement.api import todos


_MAP_DOCTYPES = (
    "QBO Vendor Map",
    "QBO Item Map",
    "QBO Account Map",
    "QBO Purchase Tax Map",
)


@frappe.whitelist(methods=["GET"])
def get_unapproved_summary() -> dict:
    return {
        doctype: frappe.db.count(doctype, filters={"approved": 0})
        for doctype in _MAP_DOCTYPES
    }


@frappe.whitelist(methods=["POST"])
def upsert_unapproved_map_row(doctype: str, payload: dict) -> dict:
    if doctype not in _MAP_DOCTYPES:
        frappe.throw(f"Unknown map doctype {doctype!r}.")
    if not isinstance(payload, dict):
        frappe.throw("`payload` must be an object.")

    primary_field = _primary_field_for(doctype)
    primary_value = payload.get(primary_field)
    if not primary_value:
        frappe.throw(f"Missing required field {primary_field!r} for {doctype}.")

    existing_name = _find_existing(doctype, payload)
    if existing_name:
        return {"doctype": doctype, "name": existing_name, "action": "skipped_existing"}

    doc = frappe.new_doc(doctype)
    for key, value in payload.items():
        doc.set(key, value)
    doc.approved = 0
    doc.insert(ignore_permissions=True)
    todos.assign_unapproved_map_row(doctype, doc.name, str(primary_value))
    frappe.db.commit()
    return {"doctype": doctype, "name": doc.name, "action": "created_unapproved"}


def _primary_field_for(doctype: str) -> str:
    return {
        "QBO Vendor Map": "supplier",
        "QBO Item Map": "erp_item_code",
        "QBO Account Map": "erp_account",
        "QBO Purchase Tax Map": "erp_tax_template",
    }[doctype]


def _find_existing(doctype: str, payload: dict) -> str | None:
    if doctype == "QBO Purchase Tax Map":
        existing = frappe.get_all(
            doctype,
            filters={
                "erp_tax_template": payload.get("erp_tax_template"),
                "erp_tax_account_head": payload.get("erp_tax_account_head"),
                "erp_tax_rate": payload.get("erp_tax_rate"),
            },
            pluck="name",
            limit=1,
        )
        return existing[0] if existing else None

    primary_field = _primary_field_for(doctype)
    return frappe.db.exists(doctype, payload[primary_field])
