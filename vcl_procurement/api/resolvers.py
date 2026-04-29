"""Mapping resolvers — pure DB lookups against the 4 QBO Map doctypes.

Each resolver returns a dict {qbo_id, qbo_name, approved, map_name} or None
if no map row exists for the given key. Staging callers use these to decide
whether to BLOCK a queue row (no map / unapproved) or proceed.
"""

from __future__ import annotations

from typing import Any

import frappe


def resolve_supplier(supplier: str) -> dict[str, Any] | None:
    if not supplier:
        return None
    row = frappe.db.get_value(
        "QBO Vendor Map",
        {"supplier": supplier},
        ["name", "qbo_vendor", "approved"],
        as_dict=True,
    )
    if not row:
        return None
    qbo_name = frappe.db.get_value("QBO Vendor", row.qbo_vendor, "display_name") if row.qbo_vendor else None
    return {
        "qbo_id": row.qbo_vendor,
        "qbo_name": qbo_name,
        "approved": bool(row.approved),
        "map_name": row.name,
    }


def resolve_item(erp_item_code: str) -> dict[str, Any] | None:
    if not erp_item_code:
        return None
    row = frappe.db.get_value(
        "QBO Item Map",
        {"erp_item_code": erp_item_code},
        ["name", "qbo_item", "approved"],
        as_dict=True,
    )
    if not row:
        return None
    qbo_name = frappe.db.get_value("QBO Item", row.qbo_item, "item_name") if row.qbo_item else None
    return {
        "qbo_id": row.qbo_item,
        "qbo_name": qbo_name,
        "approved": bool(row.approved),
        "map_name": row.name,
    }


def resolve_account(erp_account: str) -> dict[str, Any] | None:
    if not erp_account:
        return None
    row = frappe.db.get_value(
        "QBO Account Map",
        {"erp_account": erp_account},
        ["name", "qbo_account", "approved"],
        as_dict=True,
    )
    if not row:
        return None
    qbo_name = frappe.db.get_value("QBO Account", row.qbo_account, "fully_qualified_name") if row.qbo_account else None
    return {
        "qbo_id": row.qbo_account,
        "qbo_name": qbo_name,
        "approved": bool(row.approved),
        "map_name": row.name,
    }


def resolve_tax(erp_tax_template: str, erp_tax_account_head: str, erp_tax_rate: float) -> dict[str, Any] | None:
    if not (erp_tax_template and erp_tax_account_head):
        return None
    row = frappe.db.get_value(
        "QBO Purchase Tax Map",
        {
            "erp_tax_template": erp_tax_template,
            "erp_tax_account_head": erp_tax_account_head,
            "erp_tax_rate": erp_tax_rate or 0,
        },
        ["name", "qbo_taxcode", "approved"],
        as_dict=True,
    )
    if not row:
        return None
    qbo_name = frappe.db.get_value("QBO Tax Code", row.qbo_taxcode, "tax_code_name") if row.qbo_taxcode else None
    return {
        "qbo_id": row.qbo_taxcode,
        "qbo_name": qbo_name,
        "approved": bool(row.approved),
        "map_name": row.name,
    }
