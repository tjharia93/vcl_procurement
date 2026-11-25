import copy
import unittest
import types
from unittest.mock import patch
from vcl_procurement.tests.test_reel_intake import frappe, Doc, _Thrown
from vcl_procurement import reel_identity_print as layout, reel_label_api as api, po_reel_intake


class IdentityLayout(unittest.TestCase):
    def setUp(self):
        self.row=dict(batch_id='VCL-RL-2026-00569',item_code='AMF1601-110',item_name='AMF1601 - PP White - 110 mm',
            supplier='Avery <script>',purchase_order='PO-1',width_mm=110,gsm=0,qr_uri='QR',
            original_qty=999,supplier_invoice='SI-1',supplier_reel_no='MILL-1',received_on='2026-11-12')
        self.data=dict(receipt='PO-1',docstatus=1,reels=[self.row])

    def qr_text(self, html):
        import base64, re
        sources=re.findall(r'<img class="qr" alt="Reel ID" src="data:image/svg\+xml;base64,([^"]+)"',html)
        return [base64.b64decode(s).decode() for s in sources]

    def test_label_prints_no_supplier_po_or_invoice_and_its_qr_holds_only_the_reel_id(self):
        """Tanuj, 25/11/2026: the label leaves with the reel, so nothing commercial and no ERP address."""
        full=layout.labels_html(self.data)
        self.assertIn('@page{size:100mm 70mm',full)
        html=full[full.index('<section'):]  # the label itself; the source name stays in the screen toolbar
        for absent in ('999','SI-1','Avery','PO-1','VIMIT','Vimit','<span>SUPPLIER</span>','<span>PO</span>','http','/compass/',
                       'SCAN REEL RECORD','MOVEMENT','<table','<script>'):
            self.assertNotIn(absent,html)
        for text in ('<h2 style="font-size:13pt">PP White</h2>',self.row['batch_id'],
                     'SCAN IN COMPASS','<span>MILL REEL NO.</span><b>MILL-1</b>','<span>RECEIVED</span><b>12/11/2026</b>'):
            self.assertIn(text,html)
        self.assertEqual(self.qr_text(html),[self.row['batch_id']])
        self.assertEqual(html.count('<h1>'+self.row['batch_id']+'</h1>'),1)
        self.assertNotIn('0 GSM',html)

    def test_import_label_drops_bl_and_container_and_leaves_a_received_line_before_arrival(self):
        row={**self.row,'item_name':'Wood Free / Bank Paper - Reel-610-48-IN-80','item_code':'WF/BP - Reel-610-48-IN-80',
            'width_mm':610,'gsm':48,'order_type':'Import','bl_number':'BL-1','container':'CCLU7308949 / CSNU8648520',
            'supplier_reel_no':'1342542903011563','received_on':''}
        self.assertEqual(layout.material_name(row),'Wood Free / Bank Paper')
        html=layout.labels_html({**self.data,'reels':[row]})
        for absent in ('BL-1','CCLU7308949','<span>BL</span>','<span>CONTAINER</span>','Avery'):
            self.assertNotIn(absent,html)
        for text in ('48 GSM','<span>MILL REEL NO.</span><b>1342542903011563</b>','<span>RECEIVED</span><b class="write-in"></b>'):
            self.assertIn(text,html)
        no_mill=layout.labels_html({**self.data,'reels':[{**row,'supplier_reel_no':None}]})
        self.assertNotIn('MILL REEL NO.',no_mill)
        self.assertEqual(layout.label_date('2026-13-40'),'')

    def test_brightness_is_a_separate_complete_line_instead_of_a_clipped_title(self):
        row={**self.row,'item_code':'','item_name':'WOODFREE - 44GSM - 66% BRIGHTNESS','gsm':44,'width_mm':610}
        original=copy.deepcopy(row)
        self.assertEqual(layout.material_name(row),'WOODFREE')
        for format in ('label','a4'):
            html=layout.labels_html({**self.data,'reels':[row]},format)
            self.assertIn('<h2 style="font-size:13pt">WOODFREE</h2>',html)
            self.assertIn('<div class="brightness">66% BRIGHTNESS</div>',html)
            self.assertIn('data-brightness="true"',html)
            self.assertIn('44 GSM',html)
        self.assertEqual(row,original)
        self.assertEqual(layout.brightness_text({**row,'item_name':'WOODFREE - Brightness: 66.5%'}),'66.5% BRIGHTNESS')
        self.assertEqual(layout.brightness_text({**row,'item_name':'Woodfree - Reel-610-44-IN-66'}),'')
        self.assertEqual(layout.material_name({**row,'item_name':'WOODFREE - 44GSM - Coated'}),'WOODFREE - Coated')

    def test_existing_reel_purchase_note_is_explicit_and_separate_from_arrival(self):
        row={**self.row,'supplier':None,'purchase_order':None,'existing_stock':True,
            'notes':'Bought on 11/11/2026 - VCL - INDIA\nInspection note stays private',
            'received_on':'2026-11-12'}
        before=copy.deepcopy(row)
        html=layout.labels_html({**self.data,'reels':[row]})
        # The purchase date stays; the source after it (a supplier or place) is left off the label.
        self.assertIn('<span>BOUGHT ON</span><b>11/11/2026</b>',html)
        self.assertNotIn('INDIA',html)
        self.assertNotIn('Inspection note stays private',html)
        self.assertNotIn('12/11/2026',html)
        self.assertEqual(row,before)
        for notes in ('Bought on 31/02/2026 - VCL - INDIA','Inspection note only'):
            self.assertEqual(layout.purchase_reference({**row,'notes':notes}),'')
        self.assertEqual(layout.purchase_reference({**row,'existing_stock':False}),'')

    def test_only_known_measured_registration_weight_is_printed_as_net_kg(self):
        row={**self.row,'existing_stock':True,'weight_known':True,'net_kg':261.5}
        html=layout.labels_html({**self.data,'reels':[row]})
        self.assertIn('NET WEIGHT AT REGISTRATION</span> <b>261.5 kg</b>',html)
        for missing in ({'weight_known':False},{'net_kg':None},{'net_kg':0},{'net_kg':float('nan')},
                        {'existing_stock':False}):
            self.assertEqual(layout.registered_net_weight({**row,**missing}),'')
        self.assertEqual(layout.registered_net_weight({**self.row,'original_qty':261.5,'uom':'Square Meter'}),'')

    def test_quantity_box_prints_a_reels_own_known_quantity_or_stays_blank_for_handwriting(self):
        """Tanuj, 18/11/2026: weight (or length) when known for that reel, otherwise a blank box."""
        self.assertEqual(layout.quantity_box(self.row),('WEIGHT (kg)',''))
        self.assertEqual(layout.quantity_box({**self.row,'known_kg':1150.5}),('WEIGHT','1150.5 kg'))
        self.assertEqual(layout.quantity_box({**self.row,'qty_unit':'m'}),('LENGTH (m)',''))
        self.assertEqual(layout.quantity_box({**self.row,'qty_unit':'m','known_m':1000}),('LENGTH','1000 m'))
        # A kg figure never fills a metres box, and bad values leave the box blank.
        self.assertEqual(layout.quantity_box({**self.row,'qty_unit':'m','known_kg':500}),('LENGTH (m)',''))
        for bad in (0,None,'',-5,float('nan'),'abc'):
            self.assertEqual(layout.quantity_box({**self.row,'known_kg':bad}),('WEIGHT (kg)',''))
        for format in ('label','a4'):
            blank=layout.labels_html(self.data,format)
            self.assertIn('<div class="qty-box"><span>WEIGHT (kg)</span> <b></b></div>',blank)
            self.assertNotIn('999',blank)
            known=layout.labels_html({**self.data,'reels':[{**self.row,'known_kg':1150.5}]},format)
            self.assertIn('<div class="qty-box"><span>WEIGHT</span> <b>1150.5 kg</b></div>',known)

    def test_known_quantities_come_from_the_reel_record_by_the_items_stock_unit(self):
        paper={**self.row,'batch_id':'R1','item_code':'PAPER'};film={**self.row,'batch_id':'R2','item_code':'FILM'}
        other={**self.row,'batch_id':'R3'}
        reels=[Doc(name='R1',item_code='PAPER',declared_net_kg=1150.5,custom_length_m=0),
               Doc(name='R2',item_code='FILM',declared_net_kg=77,custom_length_m=1000)]
        meta=types.SimpleNamespace(has_field=lambda f:f=='custom_length_m')
        db=types.SimpleNamespace(exists=lambda *a:True,
            get_value=lambda dt,name,field:{'PAPER':'Kg','FILM':'Square Meter'}[name])
        with patch.object(frappe,'db',db,create=True),patch.object(frappe,'get_meta',return_value=meta,create=True), \
             patch.object(frappe,'get_all',return_value=reels,create=True):
            data=layout.add_known_quantities({'reels':[paper,film,other]})
        self.assertEqual((data['reels'][0].get('known_kg'),data['reels'][0].get('qty_unit')),(1150.5,None))
        self.assertEqual((data['reels'][1].get('known_m'),data['reels'][1].get('qty_unit'),data['reels'][1].get('known_kg')),(1000,'m',None))
        self.assertNotIn('known_kg',data['reels'][2])

    def test_same_layout_for_mass_and_area_stock_and_a4_uses_eight_labels(self):
        for unit in ('Kg','Tonne','Square Meter','REEL'):
            row={**self.row,'uom':unit,'original_qty':999}
            self.assertIn('height:70mm',layout.labels_html({**self.data,'reels':[row]}))
        rows=[{**self.row,'batch_id':f'TAG-{i}'} for i in range(17)]
        html=layout.labels_html({**self.data,'reels':rows},'a4')
        self.assertEqual(html.count('<article class="label-sheet">'),3)
        self.assertEqual(html.count('<section class="po-reel-label">'),17)
        self.assertNotIn('onclick',layout.native_fragment(html))
        for bad in ({**self.data,'docstatus':2},{**self.data,'reels':[]},{**self.data,'reels':[self.row,self.row]}):
            with self.assertRaises(ValueError):layout.labels_html(bad)

    def test_preview_rejects_cross_source_or_duplicate_selection_without_mutating_source(self):
        original=copy.deepcopy(self.data)
        with patch.object(api,'source_labels',return_value=self.data):
            for names in (['OTHER'],[self.row['batch_id']]*2,[],{},'invalid-json'):
                with self.assertRaises(_Thrown):api.preview('Purchase Order','PO-1',names=names)
            result=api.preview('Purchase Order','PO-1',names=[self.row['batch_id']])
        self.assertIn(self.row['batch_id'],result['html'])
        self.assertEqual(self.data,original)

    def test_native_sources_delegate_to_existing_permission_checked_readers(self):
        from vcl_procurement import reel_intake, reels
        with patch.object(reel_intake,'_gate'),patch.object(reel_intake,'intake_labels',return_value=self.data) as invoice, \
             patch.object(frappe,'db',types.SimpleNamespace(exists=lambda *a:False),create=True), \
             patch.object(reels,'receipt_labels',return_value=self.data) as receipt, \
             patch.object(po_reel_intake,'label_data',return_value=self.data) as order:
            for kind,name in [('Purchase Invoice','PI'),('Purchase Receipt','PR'),('Purchase Order','PO')]:
                self.assertIn(self.row['batch_id'],layout.reel_identity_native(name,kind))
            invoice.assert_called_once_with('PI');receipt.assert_called_once_with('PR');order.assert_called_once_with('PO')
            with self.assertRaises(_Thrown):layout.source_labels('Supplier','SUP')

    def test_batch_requires_source_permission_and_rejects_cancelled_stock_source(self):
        from vcl_procurement import reel_intake
        batch=Doc(name='B',item='ITEM',reference_doctype='Stock Entry',reference_name='SE',custom_reel_width_mm=610)
        batch.check_permission=lambda *_:None
        source=Doc(docstatus=2);source.check_permission=lambda *_:None
        db=types.SimpleNamespace(exists=lambda *a:False)
        with patch.object(reel_intake,'_gate'),patch.object(frappe,'db',db,create=True), \
             patch.object(frappe,'get_doc',side_effect=lambda dt,name:batch if dt=='Batch' else source,create=True):
            with self.assertRaisesRegex(_Thrown,'Cancelled'):layout.source_labels('Batch','B')
            def denied(*_):raise PermissionError('Company restricted')
            source.check_permission=denied
            with self.assertRaises(PermissionError):layout.source_labels('Batch','B')


class FormatMigration(unittest.TestCase):
    def test_idempotent_formats_disable_only_owned_legacy_labels_and_preserve_receiving_formats(self):
        from vcl_procurement.patches import reel_labels_100x70 as migration
        stored={}
        class Format(Doc):
            def insert(self,**kw):stored[self.name]=self;return self
            def save(self,**kw):stored[self.name]=self;return self
            def update(self,values):dict.update(self,values)
        stored['VCL Reel Labels - 100x100']=Format(name='VCL Reel Labels - 100x100',html='{{ reel_print_html(doc.name) }}',disabled=0)
        stored['VCL Reel Labels - A4 (6 labels)']=Format(name='VCL Reel Labels - A4 (6 labels)',html='User template',disabled=0)
        stored['VCL Full Reel Receiving List']=Format(name='VCL Full Reel Receiving List',html='keep',disabled=0)
        with patch.object(frappe,'db',types.SimpleNamespace(exists=lambda dt,n:n in stored),create=True), \
             patch.object(frappe,'get_doc',side_effect=lambda value,name=None:Format(value) if isinstance(value,dict) else stored[name],create=True):
            migration.execute();migration.execute()
        self.assertTrue(stored['VCL Reel Labels - 100x100'].disabled)
        self.assertFalse(stored['VCL Reel Labels - A4 (6 labels)'].disabled)
        self.assertEqual(stored['VCL Full Reel Receiving List'].html,'keep')
        self.assertEqual(len([n for n in stored if n in migration.FORMATS]),8)
        for name in migration.FORMATS:
            self.assertIn('reel_identity_native(doc.name',stored[name].html)
            self.assertNotIn('default',stored[name])
