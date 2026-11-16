"""Receipt tag preparation must preserve accounting and reuse physical IDs."""
import datetime
import types
import unittest
from unittest.mock import patch
from vcl_procurement.tests.test_reel_intake import Doc, frappe, _Thrown
from vcl_procurement import receipt_reel_tags as api


class ReceiptTags(unittest.TestCase):
    def setUp(self):
        self.tags=[];self.allocations=[];self.locks=[];self.denied=False;self.other_tags=False
        self.doc=Doc(name='PR',docstatus=0,modified='v1',company='VCL',supplier='Chentai',
            supplier_name='Chentai Paper',posting_date='2026-11-16',supplier_delivery_note='0005972',items=[
                Doc(name='r1',item_code='1525',item_name='Brown Test Liner',description='Reel Width: 1525<br>GSM: 125',
                    purchase_order='PO',purchase_order_item='p1',qty=3325,stock_qty=3325,uom='Kg',warehouse='WH3'),
                Doc(name='r2',item_code='1200',item_name='Brown Test Liner - 1200mm - 150 GSM',
                    purchase_order='PO',purchase_order_item='p2',qty=3569,stock_qty=3569,uom='Kg',warehouse='WH3')])
        self.po=Doc(name='PO',docstatus=1,company='VCL',supplier='Chentai',items=[Doc(name='p1',item_code='1525'),Doc(name='p2',item_code='1200')])
        self.item=Doc(name='Item',is_stock_item=1,has_batch_no=0,has_serial_no=0)
        def permission(*_):
            if self.denied:raise PermissionError('Not readable')
        for d in (self.doc,self.po,self.item):
            d.check_permission=permission;d.has_permission=lambda *_:True
        outer=self
        class Saved(Doc):
            def insert(self,**kwargs):
                self.name=self.reel_id;outer.tags.append(self);return self
        def get_doc(dt,name=None):
            if isinstance(dt,dict):return Saved(dt)
            if dt=='Purchase Receipt':return self.doc
            if dt=='Purchase Order':return self.po
            if dt=='Item':return self.item
            raise AssertionError(dt)
        def allocate(n):
            self.allocations.append(n);return [f'VCL-RL-2026-{i+700:05}' for i in range(n)]
        db=types.SimpleNamespace(sql=lambda q,a:self.locks.append((q,a)),
            exists=lambda dt,*a,**k:dt=='DocType' or (dt=='Reel Intake' and self.other_tags))
        patches=[patch.object(frappe,'get_doc',side_effect=get_doc,create=True),
            patch.object(frappe,'get_all',side_effect=lambda *a,**k:self.tags,create=True),
            patch.object(frappe,'db',db,create=True),patch.object(frappe,'session',types.SimpleNamespace(user='receiver'),create=True),
            patch.object(api,'_warehouse'),patch.object(api,'allocate_reel_ids',side_effect=allocate),
            patch.object(api,'nowdate',return_value='2026-11-16'),
            patch.object(api,'now_datetime',return_value=datetime.datetime(2026,11,16,16,0)),
            patch.object(api,'_qr',side_effect=lambda path:path)]
        for p in patches:p.start();self.addCleanup(p.stop)
        self.lines=[dict(receipt_item='r1',count=3,width_mm=1525,gsm=125),dict(receipt_item='r2',count=4,width_mm=1200,gsm=150)]

    def prepare(self):return api.prepare('PR',self.lines,'v1')

    def test_seven_arrivals_keep_source_lines_and_no_stock_or_weights_are_created(self):
        original=api._snapshot(self.doc)
        result=self.prepare()
        self.assertFalse(result['stock_posted']);self.assertFalse(result['reused'])
        self.assertEqual(self.allocations,[7]);self.assertEqual(len(self.tags),7)
        self.assertEqual([sum(t.purchase_order_item==p for t in self.tags) for p in ('p1','p2')],[3,4])
        for tag in self.tags:
            self.assertEqual((tag.status,tag.purchase_receipt,tag.received_on,tag.warehouse),('Arrived','PR','2026-11-16','WH3'))
            for key in ('declared_net_kg','declared_gross_kg','measured_gross_kg','core_kg','supplier_reel_no','batch'):
                self.assertIsNone(tag.get(key))
        self.assertEqual(api._snapshot(self.doc),original)
        self.assertEqual((self.doc.docstatus,self.doc.modified),(0,'v1'))
        self.assertIn('tabPurchase Order',self.locks[0][0]);self.assertIn('tabPurchase Receipt',self.locks[1][0])

    def test_read_only_preview_parses_legacy_specs_without_mutating_po(self):
        data=api.page('PR')
        self.assertEqual([(r['width_mm'],r['gsm']) for r in data['lines']],[(1525,125),(1200,150)])
        self.assertEqual((self.tags,self.allocations),([],[]))

    def test_retry_after_lost_response_reuses_ids_even_if_modified_changed(self):
        self.prepare();self.doc.modified='v2'
        self.assertTrue(self.prepare()['reused']);self.assertEqual(self.allocations,[7])
        self.lines[0]['count']=4
        with self.assertRaises(_Thrown):self.prepare()
        self.assertEqual(len(self.tags),7)

    def test_changed_source_invalidates_labels_and_retry(self):
        self.prepare()
        for key,value in (('warehouse','WH4'),('qty',3326),('item_code','other'),('purchase_order_item','other')):
            original=self.doc.items[0][key];self.doc.items[0][key]=value
            with self.assertRaises(_Thrown):api.label_data('PR')
            self.doc.items[0][key]=original
        self.doc.supplier_delivery_note='other'
        with self.assertRaises(_Thrown):api.page('PR')

    def test_cancel_return_future_date_stale_and_unreadable_refuse_preparation(self):
        for key,value in (('docstatus',1),('docstatus',2),('is_return',1),('posting_date','2026-11-17'),('modified','v2')):
            original=self.doc.get(key);self.doc[key]=value
            with self.assertRaises(_Thrown):self.prepare()
            self.doc[key]=original
        self.denied=True
        with self.assertRaises(PermissionError):self.prepare()
        self.assertEqual(self.allocations,[])

    def test_invalid_counts_specs_cross_receipt_and_duplicate_rows_are_refused(self):
        for key,values in [('count',(True,0,-1,1.2,501,'nan','inf')),('width_mm',(True,0,'nan')),('gsm',(False,0,'inf')),('receipt_item',('other',))]:
            original=self.lines[0][key]
            for value in values:
                self.lines[0][key]=value
                with self.assertRaises(_Thrown):self.prepare()
            self.lines[0][key]=original
        with self.assertRaises(_Thrown):api.prepare('PR',[self.lines[0]]*2,'v1')
        self.assertEqual(self.allocations,[])

    def test_prior_po_tags_and_tracked_items_cannot_receive_another_identity_set(self):
        self.other_tags=True
        with self.assertRaisesRegex(_Thrown,'already prepared'):self.prepare()
        self.other_tags=False;self.item.has_batch_no=1
        with self.assertRaises(_Thrown):self.prepare()
        self.item.has_batch_no=0;self.po.items[0].item_code='wrong'
        with self.assertRaises(_Thrown):self.prepare()
        self.assertEqual(self.allocations,[])

    def test_labels_and_scan_use_real_id_and_submitted_status_without_batch_balance(self):
        self.prepare();ids=[t.name for t in self.tags]
        data=api.label_data('PR');self.assertEqual([r['batch_id'] for r in data['reels']],ids)
        self.assertEqual(data['reels'][0]['qr_uri'],'/compass/reels/'+ids[0])
        self.assertEqual(len(api.label_data('PR',selected=ids[0])['reels']),1)
        self.assertIn('still a draft',api.record_page(self.tags[0])['stock_notice'])
        self.doc.docstatus=1
        self.assertIn('submitted with combined',api.record_page(self.tags[0])['stock_notice'])
        self.assertNotIn('original_qty',data['reels'][0]);self.assertEqual(self.allocations,[7])

    def test_role_and_warehouse_checks_are_not_bypassed(self):
        with patch.object(frappe,'get_roles',return_value=['Sales User']):
            with self.assertRaises(_Thrown):self.prepare()
        with patch.object(api,'_warehouse',side_effect=_Thrown('Wrong company')):
            with self.assertRaises(_Thrown):self.prepare()
        self.assertEqual(self.allocations,[])

    def test_po_reprint_cannot_bypass_receipt_permissions_or_cancellation(self):
        from vcl_procurement import po_reel_intake
        self.prepare()
        with patch.object(po_reel_intake,'source',return_value=self.po),patch.object(po_reel_intake,'rows',return_value=self.tags), \
                patch.object(api,'label_data',side_effect=PermissionError('Receipt not readable')) as labels:
            with self.assertRaises(PermissionError):po_reel_intake.label_data('PO',name=self.tags[0].name)
            labels.assert_called_once_with('PR')


if __name__=='__main__':unittest.main()
