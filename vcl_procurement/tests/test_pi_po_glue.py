"""The parts of the invoice and PO APIs that can be checked without a site:
the submit gate and the tax-row cleaning. Everything that touches a document is
checked after deploy with scripts/smoke_purchasing.py, against the real thing.

    python3 vcl_procurement/tests/test_pi_po_glue.py
"""
import datetime
import sys
import types
import unittest

frappe = types.ModuleType("frappe")


class _Thrown(Exception):
    pass


def _throw(msg, exc=None, title=None):
    raise _Thrown(msg)


frappe.whitelist = lambda *a, **k: (lambda f: f)
frappe.throw = _throw
frappe._ = lambda s: s
frappe.PermissionError = PermissionError
frappe.get_roles = lambda: ["Purchase Manager"]
utils = types.ModuleType("frappe.utils")
utils.flt = lambda v: float(v or 0)
utils.nowdate = lambda: datetime.date.today().isoformat()
utils.today = utils.nowdate
utils.date_diff = lambda a, b: 0
utils.getdate = lambda v: datetime.date.fromisoformat(str(v)[:10])
utils.add_days = lambda d, n: d
frappe.utils = utils
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", utils)

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api import pi, po, rules  # noqa: E402


class Doc(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def invoice(**kw):
    base = {"custom_purchase_invoice_type": "Importation", "bill_date": "2026-08-11",
            "custom_kra_entry_date": "2026-09-27", "custom_kra_import_number": "26MBA|M406322591"}
    base.update(kw)
    return Doc(base)


class SubmitGate(unittest.TestCase):
    def test_complete_import_invoice_passes(self):
        pi.gate_before_submit(invoice())

    def test_missing_entry_date_blocks_and_names_it(self):
        with self.assertRaises(_Thrown) as cm:
            pi.gate_before_submit(invoice(custom_kra_entry_date=None))
        self.assertIn("KRA customs entry date", str(cm.exception))

    def test_all_three_named_together(self):
        with self.assertRaises(_Thrown) as cm:
            pi.gate_before_submit(invoice(bill_date=None, custom_kra_entry_date=None, custom_kra_import_number=""))
        msg = str(cm.exception)
        for label in ("Supplier invoice date", "KRA customs entry date", "KRA customs entry number"):
            self.assertIn(label, msg)

    def test_other_types_are_not_held_to_it(self):
        for t in ("Local Purchase", "Importation costs", "Other", None):
            pi.gate_before_submit(invoice(custom_purchase_invoice_type=t, custom_kra_entry_date=None))


class TaxRows(unittest.TestCase):
    ROWS = [
        {"charge_type": "Actual", "account_head": "Insurance", "description": "Insurance", "kes": 0},
        {"charge_type": "On Previous Row Total", "row_id": 1, "account_head": "VAT - VCL", "description": "VAT", "rate": 16},
        {"charge_type": "On Item Quantity", "row_id": 3, "account_head": "MSS - VCL", "description": "MSS", "rate": 2.2656},
    ]

    def test_actual_kes_becomes_document_currency(self):
        rows = [{"charge_type": "Actual", "account_head": "VAT - VCL", "kes": 895488.45}]
        out = po.clean_tax_rows(rows, 129.61, "USD")
        self.assertAlmostEqual(out[0]["tax_amount"], 6909.1, 1)
        self.assertEqual(out[0]["base_tax_amount"], 895488.45)

    def test_kes_document_keeps_the_amount(self):
        out = po.clean_tax_rows([{"charge_type": "Actual", "account_head": "VAT", "kes": 1000}], 1, "KES")
        self.assertEqual(out[0]["tax_amount"], 1000.0)

    def test_row_id_only_kept_for_previous_row_types(self):
        out = po.clean_tax_rows(self.ROWS, 129.61, "USD")
        self.assertIsNone(out[0]["row_id"])
        self.assertEqual(out[1]["row_id"], 1)
        self.assertIsNone(out[2]["row_id"])          # per-quantity rows do not refer to a row

    def test_rows_are_numbered_from_one(self):
        self.assertEqual([r["idx"] for r in po.clean_tax_rows(self.ROWS, 1, "KES")], [1, 2, 3])

    def test_unknown_fields_are_dropped(self):
        rows = [{"charge_type": "Actual", "account_head": "X", "kes": 1, "docstatus": 1, "owner": "someone"}]
        out = po.clean_tax_rows(rows, 1, "KES")[0]
        self.assertNotIn("docstatus", out)
        self.assertNotIn("owner", out)

    def test_row_pointing_forward_is_refused(self):
        with self.assertRaises(rules.RuleError):
            po.clean_tax_rows([{"charge_type": "On Previous Row Total", "row_id": 1, "account_head": "X"}], 1, "KES")


if __name__ == "__main__":
    unittest.main(verbosity=1)
