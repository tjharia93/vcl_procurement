import datetime
import types
import unittest
from unittest.mock import patch

# The receiving test harness supplies a minimal Frappe without a local bench.
from vcl_procurement.tests.test_reels import frappe, Row, _Thrown
frappe.utils.get_url = lambda path: 'https://example.test'+path
frappe.utils.now_datetime = lambda: datetime.datetime(2026,11,7,9,0)
from vcl_procurement import reel_intake as api
from vcl_procurement.reel_intake_core import arrival_date, clean_capture, clean_preparation, package_count


class IntakeRules(unittest.TestCase):
    def test_counts_are_whole_and_materials_are_unique(self):
        self.assertEqual(clean_preparation([dict(item_code='A',count=93,width_mm=610,gsm=48)],{'A'})[0]['count'],93)
        for lines in ([dict(item_code='A',count=1.2,width_mm=610,gsm=48)],
                      [dict(item_code='A',count=1,width_mm=610,gsm=48)]*2,
                      [dict(item_code='B',count=1,width_mm=610,gsm=48)]):
            with self.assertRaises(ValueError): clean_preparation(lines,{'A'})

    def test_supplier_net_stays_separate_from_measured_gross(self):
        r=clean_capture(dict(item_code='A',declared_net_kg=500,declared_gross_kg=501),{'A'},'2026-11-07')
        self.assertEqual((r['declared_net_kg'],r['declared_gross_kg'],r['measured_gross_kg']),(500,501,0))
        with self.assertRaises(ValueError): clean_capture(dict(declared_net_kg=502,declared_gross_kg=501),set(),'2026-11-07')

    def test_arrival_needs_real_nonfuture_date(self):
        self.assertEqual(arrival_date('2026-11-07','2026-11-07'),'2026-11-07')
        for value in ('','2026-11-08','not a date'):
            with self.assertRaises(ValueError): arrival_date(value,'2026-11-07')
        self.assertEqual(package_count('64 REELS'),64)
        self.assertEqual(package_count('2 PALLETS'),0)


class Doc(Row):
    def __getattribute__(self,key):
        if key == 'items' and key in self:
            return self[key]
        return super().__getattribute__(key)
    def insert(self,**kw):
        self.name=self.get('reel_id') or self.get('batch_id')
        self.get('_store',[]).append(self)
        return self
    def save(self,**kw): return self
    def reload(self): return self


class ReceivingPrintData(unittest.TestCase):
    def test_migration_installs_only_two_opt_in_formats_and_is_repeatable(self):
        from vcl_procurement.patches import add_reel_print_formats as patcher
        saved={}
        class Format(dict):
            def insert(self,**kwargs): saved[self['name']]=self;return self
            def save(self,**kwargs): saved[self['name']]=self;return self
        def get_doc(value,name=None):
            return Format(value) if isinstance(value,dict) else saved[name]
        db=types.SimpleNamespace(exists=lambda dt,name:name in saved)
        with patch.object(frappe,'db',db,create=True),patch.object(frappe,'get_doc',side_effect=get_doc,create=True):
            patcher.execute();patcher.execute()
        self.assertEqual(set(saved),set(patcher.FORMATS))
        for name,doc in saved.items():
            self.assertEqual(doc['doc_type'],'Purchase Invoice')
            self.assertEqual(doc['margin_top'],0)
            self.assertEqual(doc['custom_format'],1)
            self.assertIn('reel_print_html(doc.name',doc['html'])
            self.assertNotIn('default',doc)

    def test_native_jinja_helper_uses_authorized_endpoints_and_rejects_bad_kind(self):
        with patch.object(api,'intake_labels',return_value={'reels':[]}) as labels:
            with self.assertRaises(_Thrown): api.reel_print_html('PI','labels')
            labels.assert_called_once_with('PI')
        with patch.object(api,'delivery_page',return_value={'reels':[]}) as delivery:
            with self.assertRaises(_Thrown): api.reel_print_html('PI','receiving')
            delivery.assert_called_once_with('PI')
        with self.assertRaises(_Thrown): api.reel_print_html('PI','anything')

    def test_only_readable_packing_list_attachments_are_returned(self):
        files = [Row(file_name='2026_PackingList_3956.pdf',file_url='/private/files/list.pdf'),
                 Row(file_name='Packing List.pdf',file_url='/files/public-list.pdf'),
                 Row(file_name='CommercialInvoice.pdf',file_url='/private/files/prices.pdf'),
                 Row(file_name='PackingList.pdf',file_url='https://other.test/file.pdf'),
                 Row(file_name='PackingList.pdf',file_url='/files/../api/method/test')]
        with patch.object(frappe,'get_list',return_value=files,create=True) as fetch:
            result=api._packing_lists('PI')
        self.assertEqual([f['file_url'] for f in result],['/private/files/list.pdf','/files/public-list.pdf'])
        self.assertEqual(fetch.call_args.kwargs['filters'],{'attached_to_doctype':'Purchase Invoice','attached_to_name':'PI'})
        self.assertEqual(fetch.call_args.kwargs['limit_page_length'],0)

    def test_full_print_data_is_authorized_and_includes_all_rows_and_delivery_qr(self):
        inv=Doc(name='PI',supplier_name='GPL',bill_no='3956',custom_bill_of_lading_number='BL',items=[])
        po=Doc(name='PO',company='VCL')
        rows=[Row(name=f'TAG-{i}') for i in range(127)]
        with patch.object(api,'_gate'),patch.object(api,'_source',return_value=(inv,po)) as source, \
             patch.object(api,'_rows',return_value=rows) as all_rows, \
             patch.object(api,'_packing_lists',return_value=[{'file_url':'/private/files/list.pdf'}]), \
             patch.object(api,'_qr',return_value='QR') as qr, \
             patch.object(frappe,'get_list',return_value=[],create=True), \
             patch.object(frappe,'get_roles',return_value=['Stock Manager']):
            result=api.delivery_page('PI','SHIP')
        source.assert_called_once_with('PI','SHIP')
        all_rows.assert_called_once_with('PI')
        self.assertEqual(len(result['reels']),127)
        self.assertEqual(result['delivery_qr_uri'],'QR')
        qr.assert_called_once_with('/compass/reel-deliveries/PI?shipment=SHIP')
        self.assertNotIn('grand_total',result)


class PreparingBySize(unittest.TestCase):
    def setUp(self):
        self.tags=[];self.batches=[];self.allocated=[];self.enabled=[]
        self.items={s:Doc(name=s,has_batch_no=0) for s in ('610','900','1010')}
        self.inv=Doc(name='PI',docstatus=1,update_stock=0,bill_no='3956',items=[Row(item_code=s,item_name='Woodfree '+s) for s in self.items])
        self.inv.custom_container_details_table=[Row(description_of_packaging_eg_51_reels_or_515_packages='64 REELS'),Row(description_of_packaging_eg_51_reels_or_515_packages='63 REELS')]
        self.po=Doc(name='PO',company='VCL',supplier='GPL')
        self.plan=[dict(item_code=s,count=n,width_mm=int(s),gsm=48) for s,n in [('610',93),('900',24),('1010',10)]]
        def make(dt,name=None):
            if isinstance(dt,dict):
                dt['_store']=self.tags if dt['doctype']=='Reel Intake' else self.batches
                return Doc(dt)
            return self.items[name]
        def enable(name): self.enabled.append(name);self.items[name].has_batch_no=1
        def allocate(count):
            self.allocated.append(count)
            return [f'VCL-RL-2026-{442+i:05d}' for i in range(count)]
        db=types.SimpleNamespace(exists=lambda *a,**k:True,sql=lambda *a,**k:None,
                                  count=lambda dt,f:1 if dt=='Stock Ledger Entry' and f['item_code']=='610' else 0)
        self.patches=[patch.object(api,'_source',return_value=(self.inv,self.po)),patch.object(api,'_rows',side_effect=lambda *a:self.tags),
            patch.object(api,'allocate_reel_ids',side_effect=allocate),patch.object(api,'enable_reel_tracking',side_effect=enable),
            patch.object(frappe,'get_doc',side_effect=make,create=True),patch.object(frappe,'db',db,create=True),
            patch.object(frappe,'get_roles',return_value=['Purchase Manager'])]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def test_127_tags_have_known_sizes_and_no_guessed_weights_or_containers(self):
        result=api.register_sizes('PI',self.plan,'SHIP')
        self.assertEqual(len(result['reels']),127)
        self.assertEqual([sum(t.item_code==s for t in self.tags) for s in self.items],[93,24,10])
        self.assertTrue(all(t.container is None and t.declared_net_kg is None and t.received_on is None for t in self.tags))
        self.assertEqual(self.enabled,['900','1010'])
        self.assertEqual(len(self.batches),34)
        self.assertTrue(all(b.item != '610' for b in self.batches))
        self.assertEqual(self.allocated,[127])
        self.assertTrue(all(b.reference_doctype == 'Reel Intake' for b in self.batches))
        self.assertFalse(result['reused'])
        # A retry must never reserve a second set of printable IDs.
        again=api.register_sizes('PI',self.plan,'SHIP')
        self.assertTrue(again['reused']);self.assertEqual(self.allocated,[127])

    def test_rejects_wrong_total_before_allocating(self):
        self.plan[0]['count']=92
        with self.assertRaises(_Thrown): api.register_sizes('PI',self.plan,'SHIP')
        self.assertEqual(self.allocated,[])

    def test_changed_material_plan_cannot_replace_printed_identities(self):
        api.register_sizes('PI',self.plan,'SHIP')
        self.plan[0]['count']=92;self.plan[1]['count']=25
        with self.assertRaises(_Thrown): api.register_sizes('PI',self.plan,'SHIP')
        self.assertEqual(self.allocated,[127])

    def test_sales_or_stock_user_alone_cannot_register_or_read_tags(self):
        for roles in (['Sales User'],['Stock User'],['Guest']):
            with patch.object(frappe,'get_roles',return_value=roles),self.assertRaises(_Thrown):api.delivery_page('PI')


class PhysicalArrival(unittest.TestCase):
    def setUp(self):
        self.doc=Doc(name='TAG',purchase_invoice='PI',purchase_order='PO',company='VCL',supplier='GPL',
                     item_code='610',width_mm=610,status='Expected',shipment='SHIP')
        self.inv=Doc(name='PI',custom_container_details_table=[Row(container_reference='CCLU7308949',description_of_packaging_eg_51_reels_or_515_packages='64 REELS')])
        self.po=Doc(company='VCL',items=[Row(item_code='610')])
        self.warehouse=Doc(company='VCL',is_group=0,disabled=0)
        self.warehouse.check_permission=lambda *a:None
        self.fields=dict(item_code='610',width_mm=610,supplier_reel_no='MILL-1',declared_net_kg=373,
                         declared_gross_kg=374,container='',received_on='2026-11-07',warehouse='Stores - VCL')
        db=types.SimpleNamespace(exists=lambda dt,*a,**k:dt=='DocType',sql=lambda *a,**k:None,count=lambda *a:0)
        patches=[patch.object(api,'_source',return_value=(self.inv,self.po)),patch.object(api,'nowdate',return_value='2026-11-07'),
                 patch.object(frappe,'get_doc',side_effect=lambda dt,*a:self.warehouse if dt=='Warehouse' else self.doc,create=True),
                 patch.object(frappe,'db',db,create=True),patch.object(frappe,'get_roles',return_value=['Purchase Manager']),
                 patch.object(frappe,'session',types.SimpleNamespace(user='receiver@example.test'),create=True)]
        for p in patches:p.start();self.addCleanup(p.stop)

    def test_optional_container_and_declared_net_do_not_post_stock(self):
        result=api.save_reel('TAG',self.fields,confirm_arrival=1)
        self.assertFalse(result['stock_posted'])
        self.assertEqual(self.doc.status,'Arrived')
        self.assertEqual(self.doc.received_by,'receiver@example.test')
        self.assertEqual(self.doc.declared_net_kg,373)
        self.assertEqual(self.doc.measured_gross_kg,0)
        self.assertIsNone(self.doc.purchase_receipt)
        self.assertIsNone(self.doc.container)

    def test_date_is_required_and_future_arrivals_are_refused(self):
        for value in ('','2026-11-08'):
            with self.assertRaises(_Thrown):api.save_reel('TAG',{**self.fields,'received_on':value},confirm_arrival=1)
        self.assertIsNone(self.doc.received_on)

    def test_retry_retains_arrival_operator_and_refuses_changed_arrival_date(self):
        api.save_reel('TAG',self.fields,confirm_arrival=1)
        first=self.doc.arrival_recorded_at
        with patch.object(frappe,'session',types.SimpleNamespace(user='another@example.test')):
            api.save_reel('TAG',self.fields,confirm_arrival=1)
        self.assertEqual(self.doc.received_by,'receiver@example.test')
        self.assertEqual(self.doc.arrival_recorded_at,first)
        with self.assertRaises(_Thrown):api.save_reel('TAG',{**self.fields,'received_on':'2026-11-06'},confirm_arrival=1)

    def test_preprinted_material_cannot_be_silently_changed(self):
        with self.assertRaises(_Thrown):api.save_reel('TAG',{**self.fields,'width_mm':900})

    def test_hold_can_be_saved_before_confirming_arrival(self):
        api.save_reel('TAG',{**self.fields,'on_hold':True})
        self.assertEqual(self.doc.status,'On hold')
        self.assertIsNone(self.doc.received_on)

    def test_stale_receiver_form_cannot_overwrite_newer_details(self):
        self.doc.modified='2026-11-07 09:05:00'
        with self.assertRaises(_Thrown):api.save_reel('TAG',self.fields,expected_modified='2026-11-07 09:00:00')
        self.assertIsNone(self.doc.supplier_reel_no)

    def test_bulk_arrival_cannot_include_another_deliveries_reel(self):
        with patch.object(api,'_rows',return_value=[self.doc]),patch.object(api,'save_reel') as save:
            with self.assertRaises(_Thrown):api.confirm_delivery_arrival('PI',['OTHER-TAG'],'2026-11-07','Stores - VCL')
            save.assert_not_called()

    def test_bulk_retry_skips_already_confirmed_reels(self):
        self.doc.received_on='2026-11-07'
        with patch.object(api,'_rows',return_value=[self.doc]),patch.object(api,'save_reel') as save:
            result=api.confirm_delivery_arrival('PI',['TAG'],'2026-11-07','Stores - VCL')
            self.assertEqual(result,{'arrivals_confirmed':0,'stock_posted':False})
            save.assert_not_called()


if __name__=='__main__':unittest.main()
