"""ACD rules, run without a bench (frappe is stubbed):  python3 vcl_procurement/tests/test_acd.py"""
import sys
import types
import unittest
from datetime import date
from unittest import mock

_f = types.ModuleType("frappe"); _f.throw = lambda *a, **k: None; _f.get_all = lambda *a, **k: []; _f.conf = {}
_u = types.ModuleType("frappe.utils")
_u.add_days = lambda d, n: d; _u.nowdate = lambda: "2026-10-12"; _u.get_url_to_form = lambda dt, n: "/" + n
_u.getdate = lambda v: v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
_u.escape_html = lambda v: v; _u.formatdate = lambda v: str(v)
_f.utils = _u
sys.modules.setdefault("frappe", _f); sys.modules.setdefault("frappe.utils", _u)

from vcl_procurement.api import acd  # noqa: E402


class _Doc(dict):
    __getattr__ = dict.get


class TestACD(unittest.TestCase):
    def test_blocks_import_without_code(self):
        with mock.patch.object(acd.frappe, "throw", side_effect=Exception("blocked")):
            with self.assertRaises(Exception):
                acd.require_acd_before_submit(_Doc(custom_purchase_invoice_type="Importation", custom_acd_reference_code="  ", custom_export_entry_received=1))

    def test_allows_import_with_code_and_local_without(self):
        acd.require_acd_before_submit(_Doc(custom_purchase_invoice_type="Importation", custom_acd_reference_code="ACD123", custom_export_entry_received=1))
        acd.require_acd_before_submit(_Doc(custom_purchase_invoice_type="Local Purchase"))

    def test_blocks_import_until_export_entry_ticked(self):
        with mock.patch.object(acd.frappe, "throw", side_effect=Exception("blocked")):
            with self.assertRaises(Exception):
                acd.require_acd_before_submit(_Doc(custom_purchase_invoice_type="Importation", custom_acd_reference_code="ACD123", custom_export_entry_received=0))

    def test_landed_invoices_drop_out(self):
        rows = [_Doc(name="A", custom_expected_date_of_delivery_to_port="2026-10-20"),
                _Doc(name="B", custom_expected_date_of_delivery_to_port="2026-10-01"),
                _Doc(name="C", custom_expected_date_of_delivery_to_port=None)]
        with mock.patch.object(acd.frappe, "get_all", return_value=rows):
            got = [r.name for r in acd.invoices_missing_acd("2026-10-12")]
        self.assertEqual(got, ["A", "C"])


if __name__ == "__main__":
    unittest.main()
