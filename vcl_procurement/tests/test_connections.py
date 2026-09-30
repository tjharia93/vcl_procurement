"""The Connections bar's ordering and status rules, without a site.

    python3 vcl_procurement/tests/test_connections.py
"""
import datetime
import sys
import types
import unittest

frappe = types.ModuleType("frappe")
frappe.whitelist = lambda *a, **k: (lambda f: f)
frappe.throw = lambda msg, exc=None, title=None: (_ for _ in ()).throw(Exception(msg))
frappe._ = lambda s: s
frappe.PermissionError = PermissionError
frappe.get_roles = lambda: ["Purchase Manager"]
utils = types.ModuleType("frappe.utils")
utils.flt = lambda v: float(v or 0)
utils.nowdate = lambda: datetime.date.today().isoformat()
utils.today = utils.nowdate
utils.getdate = lambda v: datetime.date.fromisoformat(str(v)[:10])
utils.add_days = lambda d, n: d
utils.date_diff = lambda a, b: 0
frappe.utils = utils
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", utils)

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api import connections  # noqa: E402


class Assemble(unittest.TestCase):
    def test_order_is_request_order_receipt_invoice_then_by_name(self):
        out = connections.assemble({
            "Purchase Invoice": {"ACC-PINV-2026-00595": {"ds": 1, "st": "Paid"}},
            "Purchase Receipt": {"MAT-PRE-2026-00117": {"ds": 1, "st": "Completed"}},
            "Purchase Order": {"PUR-ORD-2026-00238": {"ds": 1, "st": "Completed"}},
            "Material Request": {"MAT-MR-2026-00064": {"ds": 1, "st": "Ordered"}}})
        self.assertEqual([c["doctype"] for c in out],
                         ["Material Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice"])
        self.assertEqual([c["name"] for c in out][1:3], ["PUR-ORD-2026-00238", "MAT-PRE-2026-00117"])

    def test_names_sorted_inside_a_kind(self):
        out = connections.assemble({"Purchase Receipt": {"MAT-PRE-2026-00118": {"ds": 1}, "MAT-PRE-2026-00117": {"ds": 1}}})
        self.assertEqual([c["name"] for c in out], ["MAT-PRE-2026-00117", "MAT-PRE-2026-00118"])

    def test_draft_only_when_docstatus_is_known_to_be_zero(self):
        out = connections.assemble({"Purchase Invoice": {"A": {"ds": 0}, "B": {"ds": 1}, "C": {}}})
        self.assertEqual({c["name"]: c["draft"] for c in out}, {"A": True, "B": False, "C": False})

    def test_status_is_passed_through_and_missing_is_none(self):
        out = connections.assemble({"Purchase Order": {"P": {"ds": 1, "st": "Closed"}, "Q": {}}})
        self.assertEqual({c["name"]: c["status"] for c in out}, {"P": "Closed", "Q": None})

    def test_nothing_linked_is_an_empty_list(self):
        self.assertEqual(connections.assemble({}), [])
        self.assertEqual(connections.assemble({"Purchase Order": {}}), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
