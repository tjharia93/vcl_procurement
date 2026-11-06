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
from pathlib import Path

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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
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

    def get(self, key, default=None):
        return self.values.get(key, default)

    def __getattr__(self, key):
        return self.values.get(key)

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
        self.po = types.SimpleNamespace(name='PO', docstatus=1, supplier='Silver Star', company='VCL',items=self.po.items)
        self.po.get = lambda key, default=None: getattr(self.po, key, default)
        self.po.check_permission = lambda *a: None
        self.pr.custom_reel_request_id = None
        reels.allocate_reel_ids = lambda count: reels.next_reel_ids(count, 2026)
        reels._receipt_limits = lambda po: (1,{1:(1415,1415),2:(928,928)})

        def get_doc(arg, *a):
            if isinstance(arg, dict):
                return Batch(arg)
            if arg == 'Batch':
                return next(b for b in Batch.made if b.name == a[0])
            return self.po

        frappe.get_doc = get_doc
        frappe.db = types.SimpleNamespace(
            get_value=lambda dt, name, fields=None, as_dict=False: self.items.get(name) if dt == 'Item' else None,
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
        # An omitted PO line must never be received implicitly.
        other = [r for r in self.pr.items if r.item_code == "LINER-1250"]
        self.assertEqual(other, [])
        self.assertEqual([r.idx for r in self.pr.items], [1, 2, 3])
        self.assertTrue(self.pr.inserted)
        self.assertEqual(Batch.made[0].refs["reference_name"], "MAT-PRE-TEST")

    def test_ids_continue_the_intranet_sequence(self):
        self.assertEqual(reels.next_reel_ids(2, 2026), ["VCL-RL-2026-00442", "VCL-RL-2026-00443"])
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

    def test_tonne_receipt_preserves_rate_and_posts_correct_kg(self):
        self.po.items[0].uom = 'Tonne'
        self.po.items[0].conversion_factor = 1000
        self.pr.items[0].uom = 'Tonne'
        self.pr.items[0].conversion_factor = 1000
        self.pr.items[0].rate = 560
        reels._receipt_limits = lambda po: (1,{1:(4.687,4.687)})
        reels.receive_reels('PO', {'1':[{'declared_weight':1601,'gross_weight':1601,
             'weight_basis':'Supplier-declared weight'}]}, department='Carton')
        self.assertEqual(len(self.pr.items),1)
        self.assertEqual(self.pr.items[0].qty,1.601)
        self.assertEqual(self.pr.items[0].stock_qty,1601)
        self.assertEqual(self.pr.items[0].rate,560)
        self.assertEqual(Batch.made[0].get('custom_reel_verification'),'Pending verification')
        self.assertEqual(Batch.made[0].get('custom_reel_original_qty'),1601)

    def test_refuses_empty_selected_line(self):
        with self.assertRaises(_Thrown): reels.receive_reels('PO', {'1':[]})

    def test_refuses_invalid_inspection_or_negative_core(self):
        for value in ({'net_weight':20,'inspection_status':'made up'}, {'gross_weight':20,'core_weight':-1}):
            with self.assertRaises(_Thrown): reels.receive_reels('PO', {'1':[value]})

    def test_retry_returns_same_receipt_without_creating_more_batches(self):
        lines={'1':[{'net_weight':100}]}
        first=reels.receive_reels('PO',lines,request_id='request_123')
        stored=Row(name=self.pr.name,custom_reel_request_hash=self.pr.custom_reel_request_hash,docstatus=0)
        frappe.db.get_value=lambda dt,name,fields=None,as_dict=False: stored if dt=='Purchase Receipt' else self.items.get(name)
        old_get=frappe.get_doc
        frappe.get_doc=lambda arg,*args: self.pr if arg=='Purchase Receipt' else old_get(arg,*args)
        self.assertEqual(reels.receive_reels('PO',lines,request_id='request_123')['name'],first['name'])
        self.assertEqual(len(Batch.made),1)
        with self.assertRaises(_Thrown):
            reels.receive_reels('PO',{'1':[{'net_weight':101}]},request_id='request_123')
        self.assertEqual(len(Batch.made),1)

    def test_over_receipt_is_rejected_before_batches_are_created(self):
        with self.assertRaises(_Thrown): reels.receive_reels('PO',{'1':[{'net_weight':2000}]})
        self.assertEqual(Batch.made,[])

    def test_invoice_shipping_references_are_copied_to_each_reel(self):
        inv=types.SimpleNamespace(name='PI',supplier='Silver Star',company='VCL',docstatus=0,bill_no='SI0075290',
            items=[Row(purchase_order='PO',po_detail='poi-1')])
        inv.check_permission=lambda *a: None
        inv.get=lambda key,default=None: {'custom_bill_of_lading_number':'721700150',
            'custom_container_details_table':[Row(container_reference='cauu5292100')]}.get(key,default)
        old_get=frappe.get_doc
        frappe.get_doc=lambda arg,*args: inv if arg=='Purchase Invoice' else old_get(arg,*args)
        reels.receive_reels('PO',{'1':[{'declared_weight':100,'weight_basis':'Supplier-declared weight'}]},invoice='PI',department='Carton')
        b=Batch.made[0]
        self.assertEqual(b.get('custom_reel_supplier_invoice'),'SI0075290')
        self.assertEqual(b.get('custom_reel_bl_number'),'721700150')
        self.assertEqual(b.get('custom_reel_container'),'CAUU5292100')
        self.assertEqual(b.get('custom_department'),'Carton')
        self.assertEqual(self.pr.custom_reel_source_invoice,'PI')

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
