import copy
import datetime
import types
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from vcl_procurement.tests.test_reel_intake import frappe, Doc, _Thrown
from vcl_procurement import container_tracking as api
from vcl_procurement.container_core import preparation, allocation_key, capture
from vcl_procurement.inventory_labels import container_labels_html, reel_labels100_html


class ContainerRules(unittest.TestCase):
    def test_stock_units_and_nominal_total_never_assume_kg_per_tin(self):
        self.assertEqual(preparation(' 4 ', '20', 'Kg', 80)['count'], 4)
        self.assertEqual(preparation(4, 5, 'Litre', 20)['stock_uom'], 'Litre')
        for args in [(4, 20, 'Litre', 20), (True, 20, 'Kg', 80), (1.5, 20, 'Kg', 80),
                     (4, 'nan', 'Kg', 80), (4, 20, 'Nos', 80), (1001, 1, 'Kg', 2000)]:
            with self.assertRaises(ValueError): preparation(*args)

    def test_verified_arrival_requires_evidence_and_expired_stock_stays_held(self):
        r=capture('verify',20,'LOT-1','2027-01-01','2026-11-09','2026-11-09',True)
        self.assertEqual(r['status'],'Arrival checked')
        for kwargs in [dict(lot=''),dict(expiry='2025-01-01'),dict(expiry=''),dict(received_on='2026-11-10')]:
            args=dict(action='verify',qty=20,lot='LOT',expiry='2027-01-01',received_on='2026-11-09',today='2026-11-09',expiry_required=True)
            args.update(kwargs)
            with self.assertRaises(ValueError):capture(**args)
        self.assertEqual(capture('hold',None,'','','','2026-11-09',notes='Unreadable lot')['status'],'On hold')
        with self.assertRaises(ValueError):capture('hold',None,'','','','2026-11-09')


class ContainerAPI(unittest.TestCase):
    def setUp(self):
        self.documents={};self.seq=0;self.locks=[];self.denied=False
        self.line=Doc(name='PO-LINE-1',item_code='INK',item_name='Cyan',stock_qty=80,received_qty=0)
        self.po=Doc(name='PO-1',docstatus=1,company='VCL',supplier='SUP',supplier_name='Supplier',items=[self.line])
        self.invoice=Doc(name='PI-1',docstatus=0,company='VCL',supplier='SUP',items=[Doc(purchase_order='PO-1',po_detail='PO-LINE-1')])
        self.item=Doc(name='INK',item_name='Cyan',stock_uom='Kg',item_group='Inks and Printing Chemicals',is_stock_item=1,disabled=0,has_expiry_date=0)
        self.warehouse=Doc(name='Stores',company='VCL',disabled=0,is_group=0)
        self.company=Doc(name='VCL')
        def permit(*_args):
            if self.denied:raise PermissionError('No document access')
        for d in (self.po,self.invoice,self.item,self.warehouse,self.company):
            d.check_permission=permit;d.has_permission=lambda *_a:True
        self.po.reload=lambda:self.po
        outer=self
        class Saved(Doc):
            def insert(self,**_kwargs):
                self.name=self.get('allocation_id') or self.get('container_id');self.modified='v1'
                outer.documents[(self.doctype,self.name)]=self
                return self
            def check_permission(self,*args):permit()
            def save(self,**kwargs):self.modified='v'+str(int(str(self.modified)[1:])+1);return self
        def get_doc(dt,name=None):
            if isinstance(dt,dict):return Saved(dt)
            fixed={'Purchase Order':self.po,'Purchase Invoice':self.invoice,'Item':self.item,'Warehouse':self.warehouse,'Company':self.company}
            return fixed[dt] if dt in fixed else self.documents[(dt,name)]
        def rows(dt,filters,fields,**kw):
            return [{k:d.get(k) for k in fields} for (kind,_),d in self.documents.items()
                    if kind==dt and all(d.get(k)==v for k,v in filters.items())]
        def series(prefix,n):self.seq+=1;return str(self.seq).zfill(n)
        naming=types.ModuleType('frappe.model.naming');naming.getseries=series
        db=types.SimpleNamespace(exists=lambda dt,name:dt=='DocType' or (dt,name) in self.documents,
            get_value=lambda *args:None,sql=lambda q,args:self.locks.append(q))
        patches=[patch.object(frappe,'get_roles',return_value=['Stock Manager']),
            patch.object(frappe,'db',db,create=True),patch.object(frappe,'get_doc',side_effect=get_doc,create=True),
            patch.object(frappe,'get_all',side_effect=rows,create=True),patch.object(frappe,'get_list',return_value=['Stores'],create=True),
            patch.object(frappe,'ValidationError',_Thrown,create=True),patch.object(frappe,'conf',{},create=True),
            patch.object(frappe,'session',types.SimpleNamespace(user='checker@test'),create=True),
            patch.object(api,'nowdate',return_value='2026-11-09'),patch.object(api,'now_datetime',return_value=datetime.datetime(2026,11,9,12)),
            patch.object(api,'_qr',side_effect=lambda p:'QR:'+p),patch.dict(sys.modules,{'frappe.model.naming':naming})]
        for p in patches:p.start();self.addCleanup(p.stop)

    def prepare(self,**kwargs):return api.register('PO-1','PO-LINE-1',4,20,**kwargs)

    def test_po_first_and_invoice_first_return_the_same_container_ids(self):
        first=self.prepare();again=self.prepare(invoice='PI-1')
        self.assertTrue(again['reused']);self.assertEqual(first['containers'],again['containers'])
        self.assertEqual(self.seq,4);self.assertEqual(first['containers'][0]['name'],'VCL-IC-2026-00001')
        self.assertEqual(set(k[0] for k in self.documents),{'Container Allocation','Stock Container'})
        self.assertFalse(first['stock_posted']);self.assertTrue(any('for update' in q for q in self.locks))
        self.assertEqual(self.documents[('Container Allocation',first['allocation'])].purchase_invoice,'PI-1')
        self.assertEqual(len(api.delivery_page('PO-1','PI-1')['lines'][0]['containers']),4)

    def test_invoice_first_is_reused_from_po_and_changed_packing_is_refused(self):
        first=self.prepare(invoice='PI-1');self.assertTrue(self.prepare()['reused'])
        with self.assertRaises(_Thrown):api.register('PO-1','PO-LINE-1',2,40)
        self.assertEqual(self.seq,4)
        self.assertEqual(first['allocation'],allocation_key('PO-1','PO-LINE-1'))

    def test_roles_source_links_permissions_and_legacy_receipts_are_gated(self):
        with patch.object(frappe,'get_roles',return_value=['Guest']):
            with self.assertRaises(_Thrown):self.prepare()
        self.denied=True
        with self.assertRaises(PermissionError):self.prepare()
        self.denied=False;self.invoice.items[0].po_detail='OTHER'
        with self.assertRaises(_Thrown):self.prepare(invoice='PI-1')
        self.invoice.items[0].po_detail='PO-LINE-1';self.line.received_qty=1
        with self.assertRaises(_Thrown):self.prepare()
        self.assertEqual(self.seq,0)

    def test_rereads_order_after_lock_before_allocating(self):
        with patch.object(Doc, 'reload', lambda doc: doc['items'][0].update(stock_qty=1)):
            with self.assertRaises(_Thrown):self.prepare()
        self.assertEqual(self.seq,0)

    def test_scan_and_reprint_are_read_only_and_keep_same_id(self):
        r=self.prepare();name=r['containers'][0]['name'];before=copy.deepcopy(self.documents)
        self.assertEqual(api.container_page(name)['container']['status'],'Expected')
        self.documents[('Stock Container',name)].received_qty=0
        self.assertIsNone(api.container_page(name)['container']['received_qty'])
        self.documents[('Stock Container',name)].pop('received_qty',None)
        html=api.print_labels('PO-1',name=name)['html']
        self.assertIn('QR:/compass/containers/'+name,html);self.assertIn(name,html)
        self.assertEqual(self.documents,before);self.assertEqual(self.seq,4)
        self.denied=True
        with self.assertRaises(PermissionError):api.print_labels('PO-1',name=name)

    def test_arrival_hold_recheck_and_stale_forms_do_not_post_stock(self):
        name=self.prepare()['containers'][0]['name']
        args=dict(name=name,action='verify',expected_modified='v1',received_qty=19.8,supplier_lot='LOT-1',
            received_on='2026-11-09',warehouse='Stores')
        with self.assertRaises(_Thrown):api.check_container(**{**args,'expected_modified':'old'})
        with self.assertRaises(_Thrown):api.check_container(**{**args,'warehouse':''})
        self.warehouse.company='OTHER'
        with self.assertRaises(_Thrown):api.check_container(**args)
        self.warehouse.company='VCL'
        r=api.check_container(**args)
        self.assertEqual(r['container']['received_qty'],19.8);self.assertFalse(r['stock_posted'])
        self.assertEqual(r['container']['nominal_qty'],20)
        hold=api.check_container(name,'hold','v2',notes='Damaged lid')
        self.assertEqual(hold['container']['status'],'On hold');self.assertEqual(self.seq,4)
        self.assertEqual(hold['container']['received_qty'],19.8)
        self.assertEqual(hold['container']['warehouse'],'Stores')

    def test_ten_litre_to_five_litre_replaces_unused_tags_without_reusing_ids(self):
        self.item.stock_uom='Litre';self.line.stock_qty=50
        first=api.register('PO-1','PO-LINE-1',5,10)
        original=[r['name'] for r in first['containers']]
        args=dict(po='PO-1',po_detail='PO-LINE-1',count=10,pack_qty=5,
                  reason='Supplier changed to 5 litre cans',expected_modified='v1')
        changed=api.revise_packing(**args)
        self.assertEqual(len(changed['containers']),10);self.assertEqual(changed['replaced_count'],5)
        self.assertTrue(set(original).isdisjoint(r['name'] for r in changed['containers']))
        self.assertEqual(sum(r['nominal_qty'] for r in changed['containers']),50)
        self.assertTrue(api.revise_packing(**args)['reused']);self.assertEqual(self.seq,15)
        self.assertEqual(len(api.register('PO-1','PO-LINE-1',5,10)['containers']),10)
        for name in original:
            self.assertEqual(api.container_page(name)['container']['status'],'Replaced')
            with self.assertRaises(_Thrown):api.print_labels('PO-1',name=name)
            with self.assertRaises(_Thrown):api.check_container(name,'hold','v2',notes='Cannot reactivate')

    def test_arrived_ten_litre_cans_remain_and_only_remaining_thirty_litres_change(self):
        self.item.stock_uom='Litre';self.line.stock_qty=50
        first=api.register('PO-1','PO-LINE-1',5,10)
        arrived=[r['name'] for r in first['containers'][:2]]
        for name in arrived:
            api.check_container(name,'verify','v1',received_qty=10,supplier_lot='LOT',
                received_on='2026-11-09',warehouse='Stores')
        before={name:copy.deepcopy(self.documents[('Stock Container',name)]) for name in arrived}
        r=api.revise_packing('PO-1','PO-LINE-1',6,5,'Remaining delivery in 5 litre cans','v1')
        self.assertEqual(r['replaced_count'],3);self.assertEqual(len(r['containers']),8)
        self.assertEqual(sorted(x['nominal_qty'] for x in r['containers']),[5]*6+[10]*2)
        for name in arrived:self.assertEqual(self.documents[('Stock Container',name)],before[name])
        with self.assertRaises(_Thrown):api.revise_packing('PO-1','PO-LINE-1',7,5,'Too much','v2')
        self.assertEqual(self.seq,11)

    def test_packing_change_requires_reason_current_revision_and_no_unreviewed_receipt(self):
        self.prepare()
        for reason,stamp in [('', 'v1'), ('Valid reason','old')]:
            with self.assertRaises(_Thrown):api.revise_packing('PO-1','PO-LINE-1',8,10,reason,stamp)
        self.line.received_qty=20
        with self.assertRaises(_Thrown):api.revise_packing('PO-1','PO-LINE-1',8,10,'Changed packs','v1')
        self.assertEqual(self.seq,4)


class ExactLabels(unittest.TestCase):
    def test_ic_labels_escape_material_and_retain_ids_with_exact_dimensions(self):
        row=dict(name='VCL-IC-2026-00001',item_name='<script>bad</script>',nominal_qty=20,stock_uom='Kg',qr_uri='QR')
        html=container_labels_html([row]*19,'a4')
        self.assertEqual(html.count('<article class="ic-sheet">'),2)
        self.assertEqual(html.count('VCL-IC-2026-00001'),19)
        self.assertIn('width:100mm;height:30mm',html);self.assertNotIn('<script>',html)
        self.assertIn('@page{size:100mm 30mm',container_labels_html([row]))
        self.assertNotIn('STOCK NOT POSTED',html)
        with self.assertRaises(ValueError):container_labels_html([])

    def test_reel_100_square_identity_and_log_reuse_id(self):
        r=dict(batch_id='VCL-RL-2026-00442',qr_uri='QR',item_name='Paper',width_mm=610,gsm=48,original_qty=0)
        html=reel_labels100_html(dict(reels=[r],docstatus=0))
        self.assertIn('@page{size:100mm 100mm',html);self.assertEqual(html.count('VCL-RL-2026-00442'),2)
        self.assertEqual(html.count('<tr><td>'),6);self.assertNotIn('>0 kg',html)
        with self.assertRaises(ValueError):reel_labels100_html(dict(reels=[r],docstatus=2))

    def test_identity_schemas_have_no_direct_write_or_balance_fields(self):
        import json
        root=Path(__file__).parents[1]/'reel_tracking'/'doctype'
        for name in ('stock_container','container_allocation'):
            data=json.loads((root/name/(name+'.json')).read_text())
            self.assertTrue(data['track_changes'])
            self.assertTrue(all(not p.get('write') and not p.get('create') for p in data['permissions']))
            self.assertNotIn('balance',data['field_order'])
