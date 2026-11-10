import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from vcl_procurement.tests.test_reel_intake import frappe, Doc, _Thrown
from vcl_procurement import tagged_stock as api, tagged_stock_core as rules


def plan(**kw):
    return dict(company='VCL',warehouse='Source',kind='Reel',material='Kraft',width_mm=610,gsm=48,
        count=2,observed_on='2026-11-10',**kw)


class Rules(unittest.TestCase):
    def test_unknown_weights_and_no_assumed_ream_size(self):
        self.assertIsNone(rules.plan(plan(),'2026-11-10')['net_kg'])
        with self.assertRaises(ValueError):rules.plan(plan(net_kg=100),'2026-11-10')
        p=plan();p.update(kind='Bundle',length_mm=670,sheets_per_ream=100,reams_per_bundle=5)
        self.assertEqual(rules.plan(p,'2026-11-10')['sheets_per_ream'],100)
        p['sheets_per_ream']=''
        with self.assertRaises(ValueError):rules.plan(p,'2026-11-10')

    def test_duplicates_wrong_prefix_and_invalid_numbers(self):
        for ids in [['VCL-RL-2026-00001']*2,['VCL-BND-2026-00001']*2,['wrong']]:
            with self.assertRaises(ValueError):rules.plan(plan(existing_ids=ids),'2026-11-10')
        for n in ('nan','inf',True,-1,0,201,1.5):
            p=plan();p['count']=n
            with self.assertRaises(ValueError):rules.plan(p,'2026-11-10')

    def test_dates_and_receipt_exceptions(self):
        for d in ('2026-11-11','2026-11-08','bad'):
            with self.assertRaises(ValueError):rules.event_date(d,'2026-11-10','2026-11-09')
        with self.assertRaises(ValueError):rules.receipt('Received','',4,5)
        for outcome in ('Missing','On hold','Returned'):
            with self.assertRaises(ValueError):rules.receipt(outcome,'',None,5)
            self.assertEqual(rules.receipt(outcome,'Explain',None,5),'Explain')


class Workflow(unittest.TestCase):
    def setUp(self):
        self.docs={};self.series={};self.sql=[];self.denied=False;self.clock=0
        outer=self
        class Saved(Doc):
            def check_permission(self,*args):
                if outer.denied:raise PermissionError('Denied')
            def insert(self,**kwargs):
                self.name=self.get('tag_id') or self.get('registration_id') or self.get('movement_id') or self.get('name')
                if self.get('units'):self.units=[Doc(r) for r in self.units]
                outer.docs[(self.doctype,self.name)]=self
                return self.save()
            def save(self,**kwargs):
                outer.clock+=1;self.modified=str(outer.clock);return self
        self.Saved=Saved
        def get_doc(dt,name=None):
            if isinstance(dt,dict):return Saved(dt)
            if dt=='Company':return Saved(name=name)
            if dt=='Warehouse':return Saved(name=name,company='OTHER' if name=='Other' else 'VCL',is_group=0,disabled=0)
            return self.docs[(dt,name)]
        def rows(dt,filters,fields,**kwargs):
            return [Doc({k:d.get(k) for k in fields}) for (kind,_),d in self.docs.items() if kind==dt and all(d.get(k)==v for k,v in filters.items())]
        def series(prefix,n):
            self.series[prefix]=self.series.get(prefix,0)+1
            return str(self.series[prefix]).zfill(n)
        def sql(q,args):
            self.sql.append(q)
            if 'greatest' in q:self.series[args[1]]=max(self.series[args[1]],args[0])
        db=types.SimpleNamespace(exists=lambda dt,name:dt=='DocType' or (dt,name) in self.docs,sql=sql)
        naming=types.ModuleType('frappe.model.naming');naming.getseries=series
        patches=[patch.object(frappe,'db',db,create=True),patch.object(frappe,'get_doc',side_effect=get_doc,create=True),
            patch.object(frappe,'get_all',side_effect=rows,create=True),patch.object(frappe,'get_list',side_effect=rows,create=True),
            patch.object(frappe,'get_roles',return_value=['Stock Manager']),patch.object(frappe,'session',types.SimpleNamespace(user='store@test'),create=True),
            patch.object(api,'nowdate',return_value='2026-11-10'),patch.dict(sys.modules,{'frappe.model.naming':naming}),
            patch('vcl_procurement.reels.allocate_reel_ids',side_effect=lambda count:['VCL-RL-2026-'+series('VCL-RL-2026-',5) for _ in range(count)])]
        for p in patches:p.start();self.addCleanup(p.stop)

    def register(self,p=None,reference='REG-1'):
        return api.register(reference,p or plan(),physically_verified=1)

    def dispatch(self,units=None,reference='MOVE-1',**kwargs):
        if units is None:units=[dict(name=d.name,modified=d.modified) for (dt,_),d in self.docs.items() if dt==api.UNIT]
        args=dict(reference=reference,company='VCL',source='Source',target='Target',dispatched_on='2026-11-10',custodian='Driver',units=units)
        args.update(kwargs);return api.dispatch(**args)

    def receive(self,tag,outcome='Received',**kwargs):
        args=dict(name='MOVE-1',tag=tag,outcome=outcome,received_on='2026-11-10',expected_modified=self.docs[(api.MOVE,'MOVE-1')].modified)
        args.update(kwargs);return api.receive(**args)

    def test_registration_retry_returns_same_ids_and_never_posts_stock(self):
        first=self.register();again=self.register()
        self.assertEqual(first['units'],again['units']);self.assertTrue(again['reused'])
        self.assertEqual({d for d,_ in self.docs},{api.REG,api.UNIT})
        p=plan();p['width_mm']=900
        with self.assertRaises(_Thrown):self.register(p)

    def test_old_id_reuse_advances_series_and_duplicate_registration_rejected(self):
        p=plan(existing_ids=['VCL-RL-2026-00400','VCL-RL-2026-00401'])
        r=self.register(p)
        self.assertEqual(r['units'][0]['name'],'VCL-RL-2026-00400')
        self.assertEqual(self.series['VCL-RL-2026-'],401)
        with self.assertRaises(_Thrown):self.register(p,'REG-2')

    def test_register_requires_physical_attestation_and_scope(self):
        with self.assertRaises(_Thrown):api.register('REG',plan())
        self.denied=True
        with self.assertRaises(PermissionError):self.register()

    def test_existing_delivery_must_have_arrived_and_specs_match(self):
        p=plan(existing_ids=['VCL-RL-2026-00400','VCL-RL-2026-00401'])
        self.docs[('Reel Intake','VCL-RL-2026-00400')]=self.Saved(company='VCL',width_mm=610,gsm=48,status='Expected')
        with self.assertRaises(_Thrown):self.register(p)
        d=self.docs[('Reel Intake','VCL-RL-2026-00400')];d.received_on='2026-11-09';d.warehouse='Source';d.width_mm=900
        with self.assertRaises(_Thrown):self.register(p)
        d.width_mm=610
        r=self.register(p)
        self.assertEqual(r['units'][0]['source_doctype'],'Reel Intake')

    def test_dispatch_retry_cannot_double_move_and_clears_source_location(self):
        self.register();units=[dict(name=d.name,modified=d.modified) for (dt,_),d in self.docs.items() if dt==api.UNIT]
        self.dispatch(units);self.assertTrue(self.dispatch(units)['reused'])
        u=self.docs[(api.UNIT,units[0]['name'])]
        self.assertEqual(u.status,'In transit');self.assertIsNone(u.warehouse)
        with self.assertRaises(_Thrown):self.dispatch(reference='MOVE-2')
        self.assertIn('for update',' '.join(self.sql))

    def test_wrong_source_target_stale_or_duplicate_selection_refused(self):
        self.register();u=next(d for (dt,_),d in self.docs.items() if dt==api.UNIT)
        for kwargs in [dict(source='Target'),dict(target='Source'),dict(target='Other'),
            dict(units=[dict(name=u.name,modified='stale')]),dict(units=[dict(name=u.name,modified=u.modified)]*2)]:
            with self.assertRaises(_Thrown):self.dispatch(**kwargs)
        self.assertEqual(u.status,'Available')

    def test_partial_missing_then_found_closes_movement(self):
        r=self.register();tags=[u['name'] for u in r['units']];self.dispatch()
        self.receive(tags[0]);self.receive(tags[1],'Missing',note='Not unloaded')
        self.assertEqual(self.docs[(api.UNIT,tags[1])].status,'In transit')
        self.assertEqual(self.docs[(api.MOVE,'MOVE-1')].status,'Partly received')
        self.receive(tags[1]);self.assertEqual(self.docs[(api.MOVE,'MOVE-1')].status,'Completed')
        self.assertEqual(self.docs[(api.UNIT,tags[1])].warehouse,'Target')
        with self.assertRaises(_Thrown):self.receive(tags[1])

    def test_return_to_source_is_held_until_reviewed(self):
        tag=self.register()['units'][0]['name'];self.dispatch()
        self.receive(tag,'Returned',note='Vehicle turned back')
        u=self.docs[(api.UNIT,tag)];self.assertEqual((u.warehouse,u.status),('Source','On hold'))
        with self.assertRaises(_Thrown):api.release_hold(tag,u.modified,'2026-11-10','')
        api.release_hold(tag,u.modified,'2026-11-10','Inspected, undamaged')
        self.assertEqual(u.status,'Available')

    def test_bundle_actual_count_required_and_hold_blocks_next_move(self):
        p=plan();p.update(kind='Bundle',length_mm=670,sheets_per_ream=100,reams_per_bundle=5)
        tag=self.register(p)['units'][0]['name'];self.dispatch()
        with self.assertRaises(_Thrown):self.receive(tag,checked_reams=4)
        self.receive(tag,'On hold',note='Only four reams')
        u=self.docs[(api.UNIT,tag)]
        with self.assertRaises(_Thrown):api.release_hold(tag,u.modified,'2026-11-10','Count still four',checked_reams=4)
        self.assertEqual(u.status,'On hold')

    def test_receipt_stale_and_foreign_tag_refused(self):
        tag=self.register()['units'][0]['name'];self.dispatch()
        with self.assertRaises(_Thrown):self.receive(tag,expected_modified='old')
        with self.assertRaises(_Thrown):self.receive('FOREIGN')
        with self.assertRaises(_Thrown):self.receive(tag,received_on='2026-11-09')

    def test_receipt_accepts_count_with_spaces_without_reinterpreting_it(self):
        p=plan();p.update(kind='Bundle',length_mm=670,sheets_per_ream=100,reams_per_bundle=1000)
        tag=self.register(p)['units'][0]['name'];self.dispatch()
        self.receive(tag,checked_reams='1,000')
        self.assertEqual(self.docs[(api.MOVE,'MOVE-1')].units[0].checked_reams,1000)

    def test_new_label_links_and_prints_do_not_create_documents(self):
        tag=self.register()['units'][0]['name'];before=len(self.docs)
        with patch('vcl_procurement.reel_intake._qr',return_value='data:test') as qr:
            html=api.print_labels(name=tag)['html']
            qr.assert_called_once_with('/compass/stock-tags/'+tag)
        self.assertIn('100mm 100mm',html);self.assertIn('Weight not yet measured',html)
        self.assertNotIn('0 kg',html);self.assertEqual(len(self.docs),before)

    def test_guest_roles_cannot_register_and_schemas_have_no_direct_write(self):
        with patch.object(frappe,'get_roles',return_value=['Guest']):
            with self.assertRaises(Exception):api.options()
        root=Path(api.__file__).parent/'reel_tracking'/'doctype'
        for name in ('physical_stock_unit','stock_tag_registration','tagged_stock_movement'):
            schema=json.loads((root/name/(name+'.json')).read_text())
            self.assertTrue(all(not p.get('write') and not p.get('create') for p in schema['permissions']))


if __name__=='__main__':unittest.main()
