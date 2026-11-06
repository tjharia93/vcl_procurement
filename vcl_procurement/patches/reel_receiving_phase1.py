"""Identity, traceability and retry fields for paper-reel receiving."""
def execute():
    from vcl_procurement.reels import ensure_reel_fields
    ensure_reel_fields()
