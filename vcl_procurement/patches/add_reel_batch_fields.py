"""Add the reel fields to Batch (idempotent).

Also called from `install.after_install`: installing an app stamps every line of
patches.txt as done without running it, so a fresh site would otherwise never
get these fields.
"""
from vcl_procurement.reels import ensure_reel_fields


def execute():
    ensure_reel_fields()
