"""Reel receiving, tested without a bench.

The part worth asserting here is the shaping: one receipt row and one Batch per
reel, the PO-wide row replaced, IDs continuing the intranet sequence, and the
refusals (stock history, wrong UOM, duplicate supplier reel).

    python3 vcl_procurement/tests/test_reels.py
"""
import datetime
import sys
import types
import unittest

frappe = types.ModuleType("frappe")


class _Thrown(Exception):
    pass


def _throw(msg, exc=None):
    raise _Thrown(msg)


frappe.whitelist = lambda *a, **k: (lambda f: f)
frappe.throw = _throw
frappe._ = lambda s: s
frappe.PermissionError = PermissionError
frappe.get_roles = lambda: ["Purchase Manager"]

utils = types.ModuleType("frappe.utils")
utils.flt = lambda v: float(v or 0)
utils.getdate = lambda v: v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)[:10])
utils.nowdate = lambda: "2026-10-01"
utils.today = utils.nowdate
utils.add_days = utils.date_diff = lambda *a: None
frappe.utils = utils
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", utils)

erpnext_pr = types.ModuleType("erpnext.buying.doctype.purchase_order.purchase_order")
for name in ("erpnext", "erpnext.buying", "erpnext.buying.doctype", "erpnext.buying.doctype.purchase_order"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["erpnext.buying.doctype.purchase_order.purchase_order"] = erpnext_pr

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement import reels  # noqa: E402


class Row(dict):
    def __getattr__(self, k):
        return self.get(k)

    def __setattr__(self, k, v):
        self[k] = v

    def set(self, k, v):
        self[k] = v

    def as_dict(self):
        return dict(self)


class Batch:
    made = []

    def __init__(self, values):
        self.values = values
        self.name = values["batch_id"]
        self.refs = None

    def insert(self, **kw):
        Batch.made.append(self)
        return self

    def db_set(self, values):
        self.refs = values


class Receipt:
    def __init__(self, rows):
        self.items = rows
        self.name = "MAT-PRE-TEST"
        self.docstatus = 0
        self.supplier = "Silver Star"
        self.posting_date = "2026-10-01"
        self.inserted = False

    def append(self, _f, _v):
        r = Row()
        self.items.append(r)
        return r

    def insert(self):
        self.inserted = True


class ReceivingReels(unittest.TestCase):

    def setUp(self):
        Batch.made = []
        self.po = types.SimpleNamespace(
            supplier="Silver Star",
            items=[Row(idx=1, name="poi-1", item_code="LINER-900", uom="Kg"),
                   Row(idx=2, name="poi-2", item_code="LINER-1250", uom="Kg")])
        self.pr = Receipt([Row(idx=1, purchase_order_item="poi-1", item_code="LINER-900", qty=1415, rate=100, uom="Kg"),
                           Row(idx=2, purchase_order_item="poi-2", item_code="LINER-1250", qty=928, rate=100, uom="Kg")])
        self.items = {"LINER-900": Row(has_batch_no=1, stock_uom="Kg"),
                      "LINER-1250": Row(has_batch_no=1, stock_uom="Kg")}
        self.existing_batch = False

        def get_doc(arg, *a):
            if isinstance(arg, dict):
                return Batch(arg)
            return self.po

        frappe.get_doc = get_doc
        frappe.db = types.SimpleNamespace(
            get_value=lambda dt, name, fields=None, as_dict=False: self.items.get(name),
            exists=lambda dt, filters=None: self.existing_batch,
            sql=lambda *a, **k: [[None]],
            count=lambda *a, **k: 0)
        erpnext_pr.make_purchase_receipt = lambda po: self.pr

    def test_one_receipt_row_and_one_batch_per_reel(self):
        out = reels.receive_reels("PO", {"1": [
            {"supplier_reel_no": "A1", "gross_weight": 520, "core_weight": 20},
            {"supplier_reel_no": "A2", "gross_weight": 480, "core_weight": 20},
            {"supplier_reel_no": "A3", "net_weight": 455}]})
        self.assertEqual([r["net_weight"] for r in out["reels"]], [500, 460, 455])
        self.assertEqual(len(Batch.made), 3)
        rows = [r for r in self.pr.items if r.item_code == "LINER-900"]
        self.assertEqual([r.qty for r in rows], [500, 460, 455])
        self.assertEqual([r.batch_no for r in rows], [b.name for b in Batch.made])
        self.assertTrue(all(r.use_serial_batch_fields == 1 for r in rows))
        # the line that was not received is untouched, and idx runs 1..n
        other = [r for r in self.pr.items if r.item_code == "LINER-1250"]
        self.assertEqual(other[0].qty, 928)
        self.assertEqual([r.idx for r in self.pr.items], [1, 2, 3, 4])
        self.assertTrue(self.pr.inserted)
        self.assertEqual(Batch.made[0].refs["reference_name"], "MAT-PRE-TEST")

    def test_ids_continue_the_intranet_sequence(self):
        self.assertEqual(reels.next_reel_ids(2, 2026), ["VCL-RL-2026-00374", "VCL-RL-2026-00375"])
        self.assertEqual(reels.next_reel_ids(1, 2027), ["VCL-RL-2027-00001"])

    def test_refuses_an_item_without_batch_tracking(self):
        self.items["LINER-900"] = Row(has_batch_no=0, stock_uom="Kg")
        with self.assertRaises(_Thrown):
            reels.receive_reels("PO", {"1": [{"gross_weight": 100, "core_weight": 10}]})
        self.assertEqual(Batch.made, [])

    def test_refuses_a_line_not_ordered_in_the_stock_unit(self):
        self.po.items[0].uom = "Ream"
        with self.assertRaises(_Thrown):
            reels.receive_reels("PO", {"1": [{"net_weight": 100}]})

    def test_refuses_a_reel_with_no_weight(self):
        with self.assertRaises(_Thrown):
            reels.receive_reels("PO", {"1": [{"gross_weight": 10, "core_weight": 10}]})

    def test_refuses_a_supplier_reel_already_received(self):
        self.existing_batch = True
        with self.assertRaises(_Thrown):
            reels.receive_reels("PO", {"1": [{"supplier_reel_no": "A1", "net_weight": 100}]})

    def test_refuses_the_same_supplier_reel_twice_in_one_go(self):
        with self.assertRaises(_Thrown):
            reels.receive_reels("PO", {"1": [{"supplier_reel_no": "A1", "net_weight": 100},
                                             {"supplier_reel_no": "A1", "net_weight": 90}]})


class SwitchingTrackingOn(unittest.TestCase):

    def test_refuses_an_item_that_has_stock_history(self):
        item = types.SimpleNamespace(has_batch_no=0, create_new_batch=0, save=lambda **k: None)
        frappe.get_doc = lambda *a: item
        frappe.db = types.SimpleNamespace(count=lambda *a, **k: 4)
        with self.assertRaises(_Thrown):
            reels.enable_reel_tracking("CB - Reel")
        self.assertEqual(item.has_batch_no, 0)

    def test_switches_on_for_an_item_with_no_history(self):
        saved = []
        item = types.SimpleNamespace(has_batch_no=0, create_new_batch=0, save=lambda **k: saved.append(1))
        frappe.get_doc = lambda *a: item
        frappe.db = types.SimpleNamespace(count=lambda *a, **k: 0)
        reels.enable_reel_tracking("CWB - Reel")
        self.assertEqual((item.has_batch_no, item.create_new_batch, saved), (1, 1, [1]))


if __name__ == "__main__":
    unittest.main()
