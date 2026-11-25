import types
import unittest
from unittest.mock import patch

from vcl_procurement.tests.test_reels import frappe, _Thrown
frappe.utils.now_datetime = lambda: __import__('datetime').datetime(2026, 11, 25, 9, 0)
frappe.RateLimitExceededError = type('RateLimitExceededError', (Exception,), {})
from vcl_procurement import scan, scan_core as core


class Normalize(unittest.TestCase):
    def test_id_only_codes_old_links_and_scanner_noise_all_read_the_same(self):
        for code in ('VCL-RL-2026-01010', ' vcl-rl-2026-01010\n', 'VCL-RL-2026-01010\r',
                     'https://vimitconverters.frappe.cloud/compass/stock-tags/VCL-RL-2026-01010',
                     'https://vimitconverters.frappe.cloud/compass/reels/VCL-RL-2026-01010?x=1',
                     '/compass/reels/VCL-RL-2026-01010/'):
            self.assertEqual(core.normalize(code), ('VCL-RL-2026-01010', 'RL'), code)
        self.assertEqual(core.normalize('VCL-BND-2026-00231'), ('VCL-BND-2026-00231', 'BND'))
        self.assertEqual(core.normalize('vcl-ic-2026-00044'), ('VCL-IC-2026-00044', 'IC'))
        self.assertEqual(core.normalize('LOC-SOY-W01-P01'), ('LOC-SOY-W01-P01', 'LOC'))

    def test_anything_else_is_not_a_code(self):
        for code in (None, '', 'hello', 'VCL-XX-2026-00001', 'VCL-RL-26-1', "VCL-RL-2026-01010' OR 1=1",
                     'https://evil.example/', 'A' * 400, 'PUR-ORD-2026-00175', '1342542903011563'):
            self.assertIsNone(core.normalize(code), code)


class Shapes(unittest.TestCase):
    def test_reel_shows_floor_facts_and_no_price_or_supplier(self):
        r = core.reel_intake(dict(name='VCL-RL-2026-00779', status='Expected', material_description='Newsprint',
                                  item_code='NP-Reels-610-1000-42.5-NO', width_mm=610, gsm=42.5, declared_net_kg=301,
                                  supplier_reel_no='1342542903011563', warehouse=None, received_on=None))
        self.assertTrue(r['found'])
        self.assertEqual(r['open'], [dict(label='Open reel record', url='/compass/reels/VCL-RL-2026-00779')])
        facts = dict(r['facts'])
        self.assertEqual(facts['Size'], '610 mm | 42.5 GSM')
        self.assertEqual(facts['Weight'], '301 kg')
        self.assertNotIn('Warehouse', facts)
        for field in core.SOURCES['RL'][0][1] + core.SOURCES['BND'][0][1] + core.SOURCES['IC'][0][1]:
            self.assertNotIn('supplier', field.replace('supplier_reel_no', ''))
            self.assertNotIn('rate', field)
            self.assertNotIn('price', field)

    def test_unknown_weight_and_dates_are_left_out(self):
        u = core.stock_unit(dict(name='VCL-RL-2026-01010', kind='Reel', material='Wood Free Reel', width_mm=890, gsm=50,
                                 net_kg=594, weight_known=0, status='Available', warehouse='Soykimau - Raw Material - VCL',
                                 last_event_on='2026-11-23', active_movement='MOVE-1'))
        facts = dict(u['facts'])
        self.assertNotIn('Weight', facts)
        self.assertEqual(facts['Last checked'], '23/11/2026')
        self.assertEqual(facts['In transit'], 'Yes')
        self.assertEqual(u['open'][0]['url'], '/compass/stock-tags/VCL-RL-2026-01010')


class Resolve(unittest.TestCase):
    def setUp(self):
        self.records = {('Reel Intake', 'VCL-RL-2026-00779'): dict(name='VCL-RL-2026-00779', status='Expected',
                        material_description='Newsprint', width_mm=610, gsm=42.5),
                        ('Physical Stock Unit', 'VCL-RL-2026-01010'): dict(name='VCL-RL-2026-01010', kind='Reel',
                        material='Wood Free Reel', status='Available')}
        self.allowed = set(self.records)
        self.counts = {}
        cache = types.SimpleNamespace(incrby=lambda k, n: self.counts.__setitem__(k, self.counts.get(k, 0) + n) or self.counts[k],
                                      expire=lambda *a: None)
        db = types.SimpleNamespace(exists=lambda dt, name=None: dt == 'DocType' or (dt, name) in self.records,
                                   get_value=lambda dt, name, fields, as_dict=False: self.records[(dt, name)])
        self.logged = []
        self.patches = [patch.object(frappe, 'db', db, create=True), patch.object(frappe, 'cache', lambda: cache, create=True),
            patch.object(frappe, 'session', types.SimpleNamespace(user='store@vimit.com'), create=True),
            patch.object(frappe, 'local', types.SimpleNamespace(site='test'), create=True),
            patch.object(frappe, 'get_meta', lambda dt: types.SimpleNamespace(has_field=lambda f: True), create=True),
            patch.object(frappe, 'has_permission', lambda dt, ptype, doc=None: (dt, doc) in self.allowed, create=True),
            patch.object(frappe, 'logger', lambda name: types.SimpleNamespace(info=self.logged.append), create=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_finds_reels_from_new_and_old_labels(self):
        self.assertEqual(scan.resolve('VCL-RL-2026-00779')['open'][0]['url'], '/compass/reels/VCL-RL-2026-00779')
        old = scan.resolve('https://vimitconverters.frappe.cloud/compass/stock-tags/VCL-RL-2026-01010')
        self.assertEqual(old['open'][0]['url'], '/compass/stock-tags/VCL-RL-2026-01010')
        self.assertEqual(self.logged[-1]['id'], 'VCL-RL-2026-01010')
        self.assertTrue(self.logged[-1]['found'])

    def test_unknown_forbidden_and_junk_get_the_same_answer(self):
        self.allowed.discard(('Reel Intake', 'VCL-RL-2026-00779'))
        for code in ('VCL-RL-2026-00779', 'VCL-RL-2026-99999', 'rubbish'):
            self.assertEqual(scan.resolve(code), core.NOT_FOUND)
        self.assertFalse(self.logged[-1]['found'])

    def test_guests_are_refused_and_look_ups_are_rate_limited(self):
        with patch.object(frappe, 'session', types.SimpleNamespace(user='Guest')):
            with self.assertRaises(_Thrown):
                scan.resolve('VCL-RL-2026-00779')
        for _ in range(scan.LIMIT):
            scan.resolve('VCL-RL-2026-00779')
        with self.assertRaisesRegex(_Thrown, 'Too many'):
            scan.resolve('VCL-RL-2026-00779')

    def test_location_labels_say_locations_are_not_built_yet(self):
        r = scan.resolve('LOC-SOY-W01-P01')
        self.assertFalse(r['found'])
        self.assertIn('location', r['message'])


if __name__ == '__main__':
    unittest.main()
