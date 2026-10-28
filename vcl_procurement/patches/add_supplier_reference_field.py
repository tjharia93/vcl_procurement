"""Supplier Reference No on a Purchase Invoice: the supplier's own reference, apart from bill_no (the invoice number).

Idempotent: `ensure_fields` never touches a field that exists (it was first created live on 28/10/2026).
"""

from vcl_procurement.setup_fields import ensure_fields


def execute():
    ensure_fields()
