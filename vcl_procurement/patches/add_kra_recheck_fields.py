"""Two Purchase Invoice fields for the KRA daily re-check (custom_kra_recheck, custom_kra_recheck_tries). Idempotent."""

from vcl_procurement.setup_fields import ensure_fields


def execute():
    ensure_fields()
