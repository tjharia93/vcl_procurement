import copy
import datetime
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from vcl_procurement.tests.test_reel_intake import frappe, Doc, _Thrown
from vcl_procurement import bundle_registration as api
from vcl_procurement.bundle_core import clean_plan, canonical, totals
from vcl_procurement.bundle_print import labels_html


def plan():
    return dict(company='VCL',material='Manila',width_mm=610,length_mm=670,gsm=160,
        sheets_per_ream=100,reams_per_bundle=5,intended_warehouse='Soykimau - Raw Material - VCL',
        lines=[dict(colour='Yellow',count=36),dict(colour='Green',count=24)])


class BundleRules(unittest.TestCase):
    def test_exact_contents_and_canonical_retry_order(self):
        p=clean_plan(plan())
        self.assertEqual(totals(p),dict(total_bundles=60,total_reams=300,total_sheets=30000))
        reordered=plan();reordered['lines'].reverse()
        self.assertEqual(canonical(p),canonical(clean_plan(reordered)))
        self.assertEqual(p['length_mm'],670)

    def test_invalid_counts_dimensions_and_duplicate_colours(self):
        for value in (0,-1,1.5,True,'nan','inf',None):
            p=plan();p['lines'][0]['count']=value
            with self.assertRaises(ValueError):clean_plan(p)
        p=plan();p['lines'][1]['colour']=' yellow '
        with self.assertRaises(ValueError):clean_plan(p)
        p=plan();p['width_mm']=0
        with self.assertRaises(ValueError):clean_plan(p)
        p=plan();p['lines'][0]['count']=1000
        with self.assertRaises(ValueError):clean_plan(p)


class BundleAPI(unittest.TestCase):
    def setUp(self):
        self.documents={};self.sequence=0;self.locks=[];self.denied=False
        self.company=Doc(name='VCL');self.company.check_permission=lambda *a:None
        self.warehouse=Doc(company='VCL',is_group=0,disabled=0);self.warehouse.check_permission=lambda *a:None
        outer=self
        class SavedDoc(Doc):
            def check_permission(self,*args):
                if outer.denied:raise PermissionError('Company denied')
            def insert(self,**kwargs):
                self.name=self.get('registration_key') or self.get('bundle_id') or self.get('name')
                self.modified='v1';self.status=self.get('status')
                outer.documents[(self.doctype,self.name)]=self
                return self
            def save(self,**kwargs):self.modified='v2';return self
        self.saved_class=SavedDoc
        def get_doc(dt,name=None):
            if isinstance(dt,dict):return SavedDoc(dt)
            if dt=='Company':
                if self.denied:raise PermissionError('Company denied')
                return self.company
            if dt=='Warehouse':return self.warehouse
            return self.documents[(dt,name)]
        def get_all(dt,filters,fields,**kwargs):
            assert dt=='Stock Bundle'
            return [{key:d.get(key) for key in fields} for (kind,_),d in self.documents.items()
                if kind==dt and d.registration==filters['registration']]
        def getseries(prefix,digits):self.sequence+=1;return str(self.sequence).zfill(digits)
        db=types.SimpleNamespace(exists=lambda dt,name:dt=='DocType' or (dt,name) in self.documents,
            sql=lambda query,args:self.locks.append((query,args)))
        naming=types.ModuleType('frappe.model.naming');naming.getseries=getseries
        self.patches=[patch.object(frappe,'get_roles',return_value=['Stock Manager']),
            patch.object(frappe,'db',db,create=True),patch.object(frappe,'get_doc',side_effect=get_doc,create=True),
            patch.object(frappe,'get_all',side_effect=get_all,create=True),
            patch.object(frappe,'session',types.SimpleNamespace(user='checker@test'),create=True),
            patch.object(api,'nowdate',return_value='2026-11-07'),
            patch.object(api,'now_datetime',return_value=datetime.datetime(2026,11,7,11,0)),
            patch.object(api,'_qr',side_effect=lambda path:'QR:'+path),
            patch.dict(sys.modules,{'frappe.model.naming':naming})]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def prepare(self):return api.register('SOYKIMAU-MANILA-610X670-160-INITIAL',plan())

    def test_registers_60_without_stock_and_retries_reuse_ids(self):
        first=self.prepare();ids=[r['name'] for r in first['bundles']]
        self.assertEqual(len(ids),60);self.assertEqual(len(set(ids)),60)
        self.assertEqual(sum(r['colour']=='Yellow' for r in first['bundles']),36)
        self.assertTrue(all(r['total_sheets']==500 and not r['verified_on'] for r in first['bundles']))
        self.assertEqual(ids[0],'VCL-BND-2026-00001')
        again=self.prepare()
        self.assertTrue(again['reused']);self.assertEqual(ids,[r['name'] for r in again['bundles']])
        self.assertEqual(self.sequence,60)
        self.assertEqual({dt for dt,_ in self.documents},{'Bundle Registration','Stock Bundle'})
        self.assertFalse(first['stock_posted'])
        self.assertIn('tabCompany',self.locks[0][0])

    def test_modified_plan_is_refused_before_new_ids(self):
        self.prepare();p=plan();p['length_mm']=860
        with self.assertRaises(_Thrown):api.register('SOYKIMAU-MANILA-610X670-160-INITIAL',p)
        self.assertEqual(self.sequence,60)

    def test_company_and_warehouse_permissions_are_enforced(self):
        self.denied=True
        with self.assertRaises(PermissionError):self.prepare()
        self.denied=False;self.warehouse.company='OTHER'
        with self.assertRaises(_Thrown):self.prepare()
        self.warehouse.company='VCL';self.warehouse.disabled=1
        with self.assertRaises(_Thrown):self.prepare()
        self.assertEqual(self.sequence,0)

    def test_unauthorized_role_cannot_prepare_or_read(self):
        with patch.object(frappe,'get_roles',return_value=['Guest']):
            with self.assertRaises(_Thrown):self.prepare()
            with self.assertRaises(_Thrown):api.registration_page('ANY')

    def test_scan_and_print_are_read_only_and_filtered_by_colour(self):
        first=self.prepare();name=first['bundles'][0]['name'];before=copy.deepcopy(self.documents)
        page=api.bundle_page(name);self.assertEqual(page['bundle']['status'],'Registered')
        printed=api.print_labels(first['name'],format='thermal',colour='Yellow')['html']
        self.assertEqual(printed.count('<section class="bundle-label">'),36)
        self.assertIn('/compass/bundles/',printed)
        self.assertEqual(self.documents,before)
        self.denied=True
        with self.assertRaises(PermissionError):api.print_labels(first['name'])

    def test_mismatch_hold_and_checked_counts_remain_unposted(self):
        first=self.prepare();name=first['bundles'][0]['name']
        with self.assertRaises(_Thrown):api.check_bundle(name,'verify',4,expected_modified='v1')
        with self.assertRaises(_Thrown):api.check_bundle(name,'hold',notes='',expected_modified='v1')
        with self.assertRaises(_Thrown):api.check_bundle(name,'verify',5,expected_modified='stale')
        hold=api.check_bundle(name,'hold',notes='Only four reams found',expected_modified='v1')
        self.assertEqual(hold['bundle']['status'],'On hold')
        checked=api.check_bundle(name,'verify',5,notes='Recounted and label matched',expected_modified='v2')
        self.assertEqual(checked['bundle']['status'],'Checked')
        self.assertEqual(checked['bundle']['verified_by'],'checker@test')
        self.assertEqual(checked['bundle']['checked_reams'],5)
        self.assertFalse(checked['stock_posted'])

    def test_native_print_fragment_and_format_install_are_idempotent(self):
        first=self.prepare()
        fragment=api.bundle_native_print(first['name'])
        self.assertTrue(fragment.startswith('<style>'))
        self.assertNotIn('<!doctype',fragment)
        self.assertEqual(fragment.count('<section class="bundle-label">'),60)
        from vcl_procurement.patches import add_bundle_print_formats as installer
        installer.execute();installer.execute()
        self.assertEqual(sum(dt=='Print Format' for dt,_ in self.documents),2)

    def test_size_migration_adds_100x30_and_disables_only_app_owned_old_formats(self):
        from vcl_procurement.patches import bundle_labels_100x30 as migration
        for name,html in [('VCL Bundle Labels - 43x58','{{ bundle_native_print(doc.name) }}'),
                          ('VCL Bundle Label - 43x58','User-maintained custom template')]:
            self.saved_class(dict(doctype='Print Format',name=name,html=html,disabled=0)).insert()
        migration.execute();migration.execute()
        self.assertTrue(self.documents[('Print Format','VCL Bundle Labels - 43x58')].disabled)
        self.assertFalse(self.documents[('Print Format','VCL Bundle Label - 43x58')].disabled)
        self.assertIn(('Print Format','VCL Bundle Labels - 100x30'),self.documents)
        self.assertIn(('Print Format','VCL Bundle Label - 100x30'),self.documents)


class BundlePrintLayout(unittest.TestCase):
    def test_compact_material_removes_only_the_matching_specification(self):
        from vcl_procurement.bundle_thermal_print import material_name
        row=dict(material='MANILA REAMS - 610 X 670MM 160 GSM GREEN',width_mm=610,
            length_mm=670,gsm=160,colour='GREEN')
        self.assertEqual(material_name(row),'MANILA')
        row['gsm']=180
        self.assertEqual(material_name(row),row['material'])

    def test_60_thermal_labels_or_eight_a4_pages_escape_sources_and_keep_ids(self):
        rows=[dict(name=f'SAMPLE-{i:03d}',material='Manila <script>',gsm=160,colour='Yellow',
            width_mm=610,length_mm=670,reams_per_bundle=5,sheets_per_ream=100,total_sheets=500,
            status='Registered',qr_uri='data:image/svg+xml;base64,AA==') for i in range(60)]
        thermal=labels_html(rows)
        self.assertIn('@page{size:100mm 30mm',thermal)
        self.assertEqual(thermal.count('<article class="bundle-sheet">'),60)
        self.assertNotIn('<script>',thermal)
        self.assertIn('5 REAMS / 500 SHEETS',thermal)
        self.assertEqual(labels_html(rows,'a4').count('<article class="bundle-sheet">'),8)
        for r in rows:self.assertEqual(thermal.count('<h1>'+r['name']+'</h1>'),1)
        with self.assertRaises(ValueError):labels_html([])
        with self.assertRaises(ValueError):labels_html(rows,'label')

    def test_schema_has_no_direct_create_write_or_stock_balance(self):
        root=Path(__file__).parents[1]/'reel_tracking/doctype'
        for slug in ('bundle_registration','stock_bundle'):
            schema=json.loads((root/slug/(slug+'.json')).read_text())
            self.assertTrue(schema['track_changes'])
            for permission in schema['permissions']:
                self.assertFalse(permission.get('write') or permission.get('create') or permission.get('delete'))
            fields={f['fieldname'] for f in schema['fields']}
            self.assertFalse(fields.intersection({'actual_qty','valuation_rate','stock_uom','stock_balance'}))
