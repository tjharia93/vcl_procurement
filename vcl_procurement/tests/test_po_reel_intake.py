"""PO-only preparation: identities, retry safety and the no-stock boundary."""
import copy
import types
import unittest
from unittest.mock import patch

from vcl_procurement.tests.test_reel_intake import Doc, frappe, _Thrown
from vcl_procurement import po_reel_intake as api, reel_intake


class PoPreparation(unittest.TestCase):
    def setUp(self):
        self.tags = []
        self.allocations = []
        self.locks = []
        self.denied = False
        self.po = Doc(name='PUR-ORD-2026-00288-1', docstatus=1, status='To Receive and Bill',
            company='VCL', supplier='AVERY', supplier_name='Avery Dennison', items=[
                Doc(name=f'LINE-{i}', item_code=f'{material}-{width}', item_name=f'{material} - {width} mm',
                    description=f'Reel Width: {width} mm', qty=qty, uom='Square Meter', received_qty=0,
                    custom_no_of_reels=count)
                for i, (material, width, count, qty) in enumerate([
                    ('PP White',110,20,4400), ('PP White',116,10,2320),
                    ('Thermal 60gsm',160,6,1440), ('Thermal 60gsm',240,16,5760), ('Thermal 60gsm',300,4,1800)])])
        def permission(*_a):
            if self.denied: raise PermissionError('PO not readable')
        self.po.check_permission = permission
        outer = self
        class Saved(Doc):
            def insert(self, **_kw):
                self.name = self.reel_id
                outer.tags.append(self)
                return self
        def get_doc(dt, name=None):
            if isinstance(dt, dict):
                self.assertEqual(dt['doctype'], 'Reel Intake')
                return Saved(dt)
            if dt == 'Purchase Order': return self.po
            if dt == 'Reel Intake': return next(t for t in self.tags if t.name == name)
            raise AssertionError(f'Unexpected document access: {dt}')
        def get_all(dt, **kwargs):
            self.assertEqual(dt, 'Reel Intake')
            self.assertEqual(kwargs['filters'], {'purchase_order': self.po.name})
            self.assertEqual(kwargs['limit_page_length'], 0)
            return self.tags
        def allocate(count):
            self.allocations.append(count)
            return [f'VCL-RL-TEST-{n:05d}' for n in range(count)]
        db = types.SimpleNamespace(exists=lambda dt,*a,**_kw:dt=='DocType' or (dt=='Reel Intake' and a and isinstance(a[0],str) and any(t.name==a[0] for t in self.tags)),
            sql=lambda q, args:self.locks.append((q,args)))
        patches = [patch.object(frappe,'get_doc',side_effect=get_doc,create=True),
            patch.object(frappe,'get_all',side_effect=get_all,create=True),
            patch.object(frappe,'get_roles',return_value=['Purchase Manager']),
            patch.object(frappe,'db',db,create=True), patch.object(api,'allocate_reel_ids',side_effect=allocate)]
        for p in patches: p.start(); self.addCleanup(p.stop)

    def test_56_expected_tags_keep_exact_po_line_and_unknowns_blank(self):
        original = copy.deepcopy(self.po.items)
        result = api.register(self.po.name)
        self.assertFalse(result['reused'])
        self.assertEqual(self.allocations, [56])
        self.assertEqual(len({r.name for r in self.tags}), 56)
        self.assertEqual([sum(t.purchase_order_item==r.name for t in self.tags) for r in self.po.items], [20,10,6,16,4])
        self.assertEqual([t.container_slot for t in self.tags], list(range(1,57)))
        for r in self.tags:
            self.assertEqual((r.status,r.purchase_order),('Expected',self.po.name))
            for key in ('purchase_invoice','supplier_invoice','supplier_reel_no','received_on','received_by',
                        'arrival_recorded_at','warehouse','batch','purchase_receipt','declared_net_kg'):
                self.assertIsNone(r.get(key), key)
        self.assertTrue(all(t.gsm==0 for t in self.tags[:30]))
        self.assertTrue(all(t.gsm==60 for t in self.tags[30:]))
        self.assertEqual(self.po.items, original)
        self.assertIn('for update',self.locks[0][0])

    def test_retry_reuses_same_ids_and_changed_plan_cannot_replace_them(self):
        api.register(self.po.name)
        ids = [r.name for r in self.tags]
        self.assertTrue(api.register(self.po.name)['reused'])
        self.assertEqual(ids,[r.name for r in self.tags])
        self.assertEqual(self.allocations,[56])
        self.po.items[0].custom_no_of_reels = 21
        with self.assertRaises(_Thrown): api.register(self.po.name)
        self.assertEqual(self.allocations,[56])

    def test_same_item_on_two_po_lines_keeps_line_identity(self):
        self.po.items[1].item_code = self.po.items[0].item_code
        api.register(self.po.name)
        self.assertTrue(api.register(self.po.name)['reused'])
        self.assertEqual(len(self.tags),56)

    def test_get_is_read_only_and_reel_scan_opens_po_preparation(self):
        self.assertEqual(api.order_page(self.po.name)['reels'],[])
        self.assertEqual(self.allocations,[])
        api.register(self.po.name)
        detail = reel_intake.reel_page(self.tags[0].name)
        self.assertTrue(detail['preparation_only'])
        self.assertEqual(detail['reel']['purchase_order'],self.po.name)
        with self.assertRaises(_Thrown): reel_intake.save_reel(self.tags[0].name,{})
        self.assertIsNone(self.tags[0].received_on)

    def test_read_and_registration_enforce_role_and_po_permission(self):
        self.denied = True
        with self.assertRaises(PermissionError): api.register(self.po.name)
        self.denied = False
        with patch.object(frappe,'get_roles',return_value=['Sales User']):
            with self.assertRaises(_Thrown): api.order_page(self.po.name)
        with patch.object(frappe,'get_roles',return_value=['Stock Manager']):
            self.assertFalse(api.order_page(self.po.name)['can_register'])
            with self.assertRaises(_Thrown): api.register(self.po.name)
        self.assertEqual(self.allocations,[])

    def test_bad_counts_missing_width_received_or_closed_po_are_refused(self):
        line = self.po.items[0]
        for raw in (True, 1.5, -1, 'nan', 'inf', 501):
            line.custom_no_of_reels = raw
            with self.assertRaises(_Thrown): api.register(self.po.name)
        line.custom_no_of_reels = 20
        line.received_qty = 1
        with self.assertRaises(_Thrown): api.register(self.po.name)
        line.received_qty = 0
        line.item_name='PP';line.description='PP'
        with self.assertRaises(_Thrown): api.register(self.po.name)
        self.po.status='Closed'
        with self.assertRaises(_Thrown): api.register(self.po.name)
        self.assertEqual(self.allocations,[])

    def test_invoice_route_cannot_allocate_a_second_set_for_prepared_po(self):
        inv = Doc(name='PI',items=[])
        with patch.object(reel_intake,'_source',return_value=(inv,self.po)), \
             patch.object(frappe.db,'exists',side_effect=lambda dt,*a:True), \
             patch.object(reel_intake,'allocate_reel_ids') as allocate:
            with self.assertRaisesRegex(_Thrown,'already prepared'):
                reel_intake.register_sizes('PI',[])
            allocate.assert_not_called()

    def test_po_label_reads_reuse_the_registered_ids_and_authorize_the_source(self):
        with self.assertRaises(_Thrown):api.label_data(self.po.name)
        api.register(self.po.name)
        with patch.object(reel_intake,'_qr',side_effect=lambda path:path):
            all_labels=api.label_data(self.po.name)
            selected=api.label_data(self.po.name,name=self.tags[0].name)
        self.assertEqual(all_labels['source_type'],'Purchase Order')
        self.assertEqual(all_labels['source_name'],self.po.name)
        self.assertEqual(len(all_labels['reels']),56)
        self.assertEqual([r['batch_id'] for r in selected['reels']],[self.tags[0].name])
        self.assertEqual(selected['reels'][0]['qr_uri'],'/compass/reels/'+self.tags[0].name)
        self.assertEqual(self.allocations,[56])
        self.denied=True
        with self.assertRaises(PermissionError):api.label_data(self.po.name)


if __name__ == '__main__': unittest.main()
