"""Approved VCL assets embedded in print HTML so printing needs no extra fetch."""
import base64
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=2)
def logo_uri(lockup=False):
    name = 'vc_lockup.png' if lockup else 'vc_icon.png'
    data = (Path(__file__).parent / 'public' / 'images' / name).read_bytes()
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


def label_brand_css():
    # Compact thermal mark: preserve all label/QR dimensions and printable contrast.
    return '.brand .vcl-print-mark{position:static;display:inline-block;width:3mm;height:3mm;margin-right:1mm;vertical-align:middle;filter:grayscale(1)}'


def brand_icon():
    return '<img class="vcl-print-mark" alt="VCL" src="'+logo_uri()+'">'
