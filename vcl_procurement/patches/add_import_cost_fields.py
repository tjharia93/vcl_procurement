"""Journal Entry and Purchase Invoice fields for the import costs screen (customs entry, deposit recovery). Idempotent."""

from vcl_procurement.setup_fields import ensure_fields


def execute():
    ensure_fields()
