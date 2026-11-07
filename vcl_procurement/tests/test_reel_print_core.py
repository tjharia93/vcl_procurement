import unittest
from vcl_procurement.reel_print_core import labels_html, receiving_list_html


class NativePrintLayouts(unittest.TestCase):
    def setUp(self):
        self.reels = []
        for width, count in ((610,93),(900,24),(1010,10)):
            for _ in range(count):
                self.reels.append(dict(name=f'VCL-RL-2026-{442+len(self.reels):05d}',
                    item_code=str(width),width_mm=width,gsm=48,status='Expected'))
        self.delivery=dict(invoice='PI',supplier='GPL <script>',supplier_invoice='3956',po='PO',
            containers=[dict(number='CCLU7308949'),dict(number='CSNU8648520')],reels=self.reels,
            items=[dict(item_code=str(w),item_name=f'Woodfree {w}',qty=q,uom='Tonne')
                   for w,q in ((610,34.691),(900,13.132),(1010,6.249))])
        self.labels=dict(receipt='PI',docstatus=0,reels=[dict(batch_id=r['name'],item_code=r['item_code'],
            width_mm=r['width_mm'],gsm=48,pre_registered=True,physical_status='Expected',original_qty=0,
            supplier='GPL <script>',supplier_invoice='3956',order_type='Import',bl_number='COSU6470305350',
            container='CCLU7308949 / CSNU8648520',container_label='Shipment containers',
            weight_basis='Supplier label net / gross',verification='Verify on arrival',
            qr_uri='data:image/svg+xml;base64,AA==') for r in self.reels])

    def test_three_pairs_per_sheet_and_six_movement_rows_reuse_all_ids(self):
        html=labels_html(self.labels)
        self.assertEqual(html.count('<article class="reel-sheet">'),43)
        self.assertEqual(html.count('<div class="reel-pair">'),127)
        self.assertEqual(html.count('<tr><td></td>'),762)
        self.assertNotIn('<script>',html)
        self.assertNotIn('>0 kg',html)
        self.assertNotIn('EXPECTED',html)
        self.assertIn('RW 610 mm \u00b7 48 GSM',html)
        for r in self.reels:
            self.assertEqual(html.count('<h1>'+r["name"].replace("VCL-RL-", "VCL/").replace("-", "/")+'</h1>'),2)
        self.assertIn('width:100mm;height:95.666mm',html)
        self.assertIn('Received on (date)',html)
        self.assertIn('CCLU7308949 / CSNU8648520',html)

    def test_full_list_has_nine_pages_and_each_existing_reel_once(self):
        html=receiving_list_html(self.delivery)
        self.assertEqual(html.count('<article class="reel-sheet">'),9)
        self.assertNotIn('<script>',html)
        self.assertIn('Invoice quantity: 34.691 Tonne',html)
        self.assertIn('Supplier label<br>NET kg',html)
        self.assertIn('Measured<br>GROSS kg',html)
        self.assertNotIn('>0</td>',html)
        for r in self.reels:
            self.assertEqual(html.count(f'<td class="id">{r["name"]}</td>'),1)

    def test_empty_and_cancelled_prints_are_refused(self):
        with self.assertRaises(ValueError): labels_html(dict(reels=[]))
        with self.assertRaises(ValueError): receiving_list_html(dict(reels=[]))
        with self.assertRaises(ValueError): labels_html({**self.labels,'docstatus':2})

    def test_saved_declarations_and_measured_weights_are_not_merged(self):
        r={**self.reels[0], 'supplier_reel_no':'MILL-1','declared_net_kg':373,
           'declared_gross_kg':374,'measured_gross_kg':375,'received_on':'2026-11-07',
           'notes':'<damaged>','status':'On hold'}
        html=receiving_list_html({**self.delivery,'reels':[r]})
        for text in ('MILL-1','>373</td>','>374</td>','>375</td>','2026-11-07','ON HOLD','&lt;damaged&gt;'):
            self.assertIn(text,html)
