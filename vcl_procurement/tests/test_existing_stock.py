"""Phase S: item links by Tanuj, signed tagging sheets, opening and transfer drafts. No bench."""
import sys
import types
import unittest
from unittest.mock import patch

from vcl_procurement.tests.test_reel_intake import frappe, Doc, _Thrown
from vcl_procurement.tests import test_tagged_stock as tts
from vcl_procurement import existing_stock as api, existing_stock_core as rules, tagged_stock


def plan(**kw):
    p = tts.plan(); p.update(kw); return p


def reel(**kw):
    u = dict(name='VCL-RL-2026-00001', kind='Reel', weight_known=1, net_kg=500)
    u.update(kw); return u


class Rules(unittest.TestCase):
    def test_reel_quantities_follow_the_item_unit_and_never_guess(self):
        self.assertEqual(rules.ledger_qty(reel(), 'Kg'), 500)
        self.assertEqual(rules.ledger_qty(reel(), 'Tonne'), 0.5)
        for uom in ('Square Meter', 'Nos', 'Ream'):
            with self.assertRaises(ValueError): rules.ledger_qty(reel(), uom)
        with self.assertRaises(ValueError): rules.ledger_qty(reel(weight_known=0), 'Kg')

    def test_bundle_and_container_quantities(self):
        b = dict(name='B', kind='Bundle', reams_per_bundle=8, sheets_per_ream=100)
        self.assertEqual((rules.ledger_qty(b, 'Ream'), rules.ledger_qty(b, 'Sheet'), rules.ledger_qty(b, 'Bundle')), (8, 800, 1))
        with self.assertRaises(ValueError): rules.ledger_qty(b, 'Kg')
        c = dict(name='C', kind='Container', stock_uom='Litre', fill_state='Full', quantity_known=1, observed_qty=20)
        self.assertEqual(rules.ledger_qty(c, 'Liter'), 20)
        self.assertEqual(rules.ledger_qty(c, 'Nos'), 1)
        with self.assertRaises(ValueError): rules.ledger_qty(c, 'Kg')
        with self.assertRaises(ValueError): rules.ledger_qty(dict(c, fill_state='Part used', observed_qty=7), 'Nos')
        with self.assertRaises(ValueError): rules.ledger_qty(dict(c, quantity_known=0), 'Litre')

    def test_sheet_rows_need_notes_for_exceptions(self):
        ok = rules.sheet_rows([dict(name='B', modified='1', outcome='Present'), dict(name='A', modified='1', outcome='Held', note='Wet')])
        self.assertEqual([r['name'] for r in ok], ['A', 'B'])
        for bad in ([dict(name='A', modified='1', outcome='Not found')], [dict(name='A', modified='1', outcome='Maybe')],
                    [dict(name='A', outcome='Present')], [dict(name='A', modified='1', outcome='Present')] * 2, []):
            with self.assertRaises(ValueError): rules.sheet_rows(bad)

    def test_voucher_lines_sum_unbatched_and_split_batched(self):
        rows = [dict(name='A', item_code='P', warehouse='W', qty=100), dict(name='B', item_code='P', warehouse='W', qty=50.5),
                dict(name='C', item_code='Q', warehouse='W', qty=3)]
        lines = rules.voucher_lines(rows, {'Q'})
        self.assertEqual([(l['item_code'], l['qty'], l['batch_no'], l['tags']) for l in lines],
                         [('P', 150.5, None, ['A', 'B']), ('Q', 3, 'C', ['C'])])

    def test_draft_request(self):
        r = rules.draft_request('Opening', '2026-11-10', '2026-11-16', {'P': '120.5'})
        self.assertEqual(r['rates'], {'P': 120.5})
        for args in [('Opening', '2026-11-10', '2026-11-16', {}), ('Opening', '2026-11-17', '2026-11-16', {'P': 1}),
                     ('Transfer', '2026-11-10', '2026-11-16', None, ''), ('Other', '2026-11-10', '2026-11-16')]:
            with self.assertRaises(ValueError): rules.draft_request(*args)


class Workflow(unittest.TestCase):
    def setUp(self):
        tts.Workflow.setUp(self)
        self.balances = {}; self.inserted = []
        self.docs[('Item', 'PAPER')] = self.Saved(doctype='Item', name='PAPER', disabled=0, is_stock_item=1, stock_uom='Kg', has_batch_no=0)
        self.docs[('Item', 'SQM')] = self.Saved(doctype='Item', name='SQM', disabled=0, is_stock_item=1, stock_uom='Square Meter', has_batch_no=0)
        self.docs[('Item', 'BATCHED')] = self.Saved(doctype='Item', name='BATCHED', disabled=0, is_stock_item=1, stock_uom='Kg', has_batch_no=1)
        base_get_doc = frappe.get_doc.side_effect
        outer = self

        def get_doc(dt, name=None):
            if isinstance(dt, dict):
                d = dict(dt)
                d.setdefault('name', d.get('sheet_id') or d.get('request_id') or d.get('batch_id')
                             or ({'Stock Reconciliation': 'MAT-RECO-1', 'Stock Entry': 'MAT-STE-1'}.get(d['doctype'])))
                doc = outer.Saved(d)
                if d['doctype'] in ('Stock Reconciliation', 'Stock Entry'): outer.inserted.append(doc)
                return doc
            return base_get_doc(dt, name)

        def get_all(dt, filters=None, fields=None, pluck=None, **kw):
            def ok(val, want):
                if isinstance(want, list) and want[0] == 'like': return str(val or '').startswith(want[1].rstrip('%'))
                if isinstance(want, list) and want[0] == 'in': return val in want[1]
                return val == want
            rows = [d for (kind, _), d in self.docs.items() if kind == dt and all(ok(d.get(k), v) for k, v in (filters or {}).items())]
            if pluck: return [d.get(pluck) for d in rows]
            return [Doc({k: d.get(k) for k in fields}) for d in rows]

        def set_value(dt, name, values, update_modified=True):
            for k, v in values.items(): self.docs[(dt, name)].set(k, v) if hasattr(self.docs[(dt, name)], 'set') else setattr(self.docs[(dt, name)], k, v)

        self.db = frappe.db
        self.db.has_column = lambda dt, col: True
        self.db.set_value = set_value
        stock_utils = types.ModuleType('erpnext.stock.utils')
        stock_utils.get_stock_balance = lambda item, wh, date=None: self.balances.get((item, wh), 0)
        sr = types.ModuleType('erpnext.stock.doctype.stock_reconciliation.stock_reconciliation')
        sr.get_difference_account = lambda purpose, company: '1910 - Temporary Opening - VCL'
        batch = types.ModuleType('erpnext.stock.doctype.batch.batch')
        batch.get_batch_qty = lambda batch_no=None, item_code=None: 0
        for p in [patch.object(frappe, 'get_doc', side_effect=get_doc), patch.object(frappe, 'get_all', side_effect=get_all),
                  patch.object(frappe, 'conf', {}, create=True), patch.object(frappe, 'get_cached_value', lambda *a: 'Main - VCL', create=True),
                  patch.object(api, 'nowdate', return_value='2026-11-16'),
                  patch.dict(sys.modules, {'erpnext': types.ModuleType('erpnext'), 'erpnext.stock.utils': stock_utils,
                      'erpnext.stock.doctype.stock_reconciliation.stock_reconciliation': sr,
                      'erpnext.stock.doctype.batch.batch': batch}),
                  patch.object(frappe, 'session', types.SimpleNamespace(user='tanuj.haria@vimit.com'))]:
            p.start(); self.addCleanup(p.stop)
        tagged_stock.register('REG-1', plan(count=1, net_kg=500), physically_verified=1)
        tagged_stock.register('REG-2', plan(count=1, net_kg=300), physically_verified=1)
        tagged_stock.register('REG-3', plan(count=2), physically_verified=1)

    def unit(self, name):
        return self.docs[(api.UNIT, name)]

    def ref(self, *names):
        return [dict(name=n, modified=self.unit(n).modified) for n in names]

    def as_user(self, user):
        p = patch.object(frappe, 'session', types.SimpleNamespace(user=user)); p.start(); self.addCleanup(p.stop)

    def sheet(self, reference, outcomes, sheet_id='SHEET-1'):
        rows = [dict(name=n, modified=self.unit(n).modified, outcome=o, note='' if o == 'Present' else 'Explained') for n, o in outcomes.items()]
        return api.record_sheet(sheet_id, reference, 'Juma', '2026-11-16', rows)

    def ready(self, treatment='Missing from books'):
        api.set_item(self.ref('VCL-RL-2026-00001', 'VCL-RL-2026-00002'), 'PAPER', treatment)
        self.sheet('REG-1', {'VCL-RL-2026-00001': 'Present'}, 'S1')
        self.sheet('REG-2', {'VCL-RL-2026-00002': 'Present'}, 'S2')

    def test_only_tanuj_sets_items_and_m2_items_are_refused_for_reels(self):
        self.as_user('store@test')
        with self.assertRaises(_Thrown): api.set_item(self.ref('VCL-RL-2026-00001'), 'PAPER', 'In books')
        self.as_user('tanuj.haria@vimit.com')
        with self.assertRaises(_Thrown): api.set_item(self.ref('VCL-RL-2026-00001'), 'SQM', 'In books')
        out = api.set_item(self.ref('VCL-RL-2026-00001'), 'PAPER', 'In books')
        u = self.unit('VCL-RL-2026-00001')
        self.assertEqual((u.item_code, u.stock_uom, u.ledger_status, u.item_set_by), ('PAPER', 'Kg', 'Already recorded', 'tanuj.haria@vimit.com'))
        self.assertFalse(out['stock_posted'])
        with self.assertRaises(_Thrown): api.set_item([dict(name='VCL-RL-2026-00001', modified='stale')], 'PAPER', 'In books')

    def test_signed_sheet_records_ticks_once_and_holds_held_units(self):
        out = self.sheet('REG-3', {'VCL-RL-2026-00003': 'Present', 'VCL-RL-2026-00004': 'Held'})
        self.assertFalse(out['reused'])
        a, b = self.unit('VCL-RL-2026-00003'), self.unit('VCL-RL-2026-00004')
        self.assertEqual((a.count_outcome, a.status, a.count_signed_by, a.counted_on), ('Present', 'Available', 'Juma', '2026-11-16'))
        self.assertEqual((b.count_outcome, b.status, b.notes), ('Held', 'On hold', 'Explained'))
        sheet = self.docs[(api.SHEET, 'SHEET-1')]
        # The same ticks again (a lost response) return the saved sheet; other ticks are refused.
        rows = [dict(name='VCL-RL-2026-00003', modified='x', outcome='Present'), dict(name='VCL-RL-2026-00004', modified='x', outcome='Held', note='Explained')]
        self.assertTrue(api.record_sheet('SHEET-1', 'REG-3', 'Juma', '2026-11-16', rows)['reused'])
        rows[1].update(outcome='Present', note='')
        with self.assertRaises(_Thrown): api.record_sheet('SHEET-1', 'REG-3', 'Juma', '2026-11-16', rows)
        self.assertIs(self.docs[(api.SHEET, 'SHEET-1')], sheet)

    def test_bulk_batch_is_one_sheet(self):
        batch = 'EXIST-BULK-097324a3-6950-4002-8d4c-271955b15262'
        rows = ['042c39a5-deda-442e-9e32-6e5ac1b2a183', '0fd67d78-8eff-4671-bc61-f989ada9cfe9']
        for i, row in enumerate(rows):
            tagged_stock.register(batch + '-' + row, plan(count=1, net_kg=100 + i), physically_verified=1)
        self.assertEqual(rules.sheet_key(batch + '-' + rows[0]), batch)
        self.assertEqual(rules.sheet_key('REG-1'), 'REG-1')
        with patch('vcl_procurement.print_brand.logo_uri', return_value='data:,'):
            html = api.tagging_sheet(batch)['html']
        self.assertIn('VCL-RL-2026-00005', html); self.assertIn('VCL-RL-2026-00006', html); self.assertNotIn('VCL-RL-2026-00001', html)
        out = self.sheet(batch, {'VCL-RL-2026-00005': 'Present', 'VCL-RL-2026-00006': 'Present'}, 'SB')
        self.assertEqual(len(out['units']), 2)
        with self.assertRaises(_Thrown): self.sheet(batch, {'VCL-RL-2026-00001': 'Present'}, 'SC')
        with self.assertRaises(_Thrown): api.tagging_sheet('EXIST-BULK-unknown')

    def test_sheet_refuses_units_from_another_registration(self):
        with self.assertRaises(_Thrown): self.sheet('REG-1', {'VCL-RL-2026-00003': 'Present'})

    def test_opening_draft_sums_units_sets_opening_account_and_links_units(self):
        self.ready()
        args = dict(kind='Opening', units=self.ref('VCL-RL-2026-00001', 'VCL-RL-2026-00002'), posting_date='2026-11-16', rates={'PAPER': 120})
        preview = api.draft_preview(**args)
        self.assertEqual(preview['blockers'], [])
        self.assertEqual([(l['item_code'], l['qty']) for l in preview['lines']], [('PAPER', 800)])
        out = api.prepare_draft('REQ-1', **args)
        sr = self.inserted[-1]
        self.assertEqual((sr.doctype, sr.purpose, sr.expense_account, sr.cost_center), ('Stock Reconciliation', 'Opening Stock', '1910 - Temporary Opening - VCL', 'Main - VCL'))
        self.assertEqual([(r['item_code'], r['qty'], r['valuation_rate']) for r in sr.items], [('PAPER', 800, 120)])
        self.assertFalse(out['stock_posted'])
        for n in ('VCL-RL-2026-00001', 'VCL-RL-2026-00002'):
            self.assertEqual((self.unit(n).stock_draft, self.unit(n).stock_draft_status), ('MAT-RECO-1', 'Draft'))
        # A retry returns the same draft; a second request for the same units is blocked.
        self.assertTrue(api.prepare_draft('REQ-1', **args)['reused']); self.assertEqual(len(self.inserted), 1)
        args['units'] = self.ref('VCL-RL-2026-00001', 'VCL-RL-2026-00002')
        with self.assertRaises(_Thrown): api.prepare_draft('REQ-2', **args)

    def test_opening_refused_over_an_existing_balance_and_for_unconfirmed_or_unweighed_units(self):
        self.ready()
        self.balances[('PAPER', 'Source')] = 10
        args = dict(kind='Opening', units=self.ref('VCL-RL-2026-00001'), posting_date='2026-11-16', rates={'PAPER': 120})
        self.assertTrue(any('overwrite' in b for b in api.draft_preview(**args)['blockers']))
        with self.assertRaises(_Thrown): api.prepare_draft('REQ-1', **args)
        api.set_item(self.ref('VCL-RL-2026-00003'), 'PAPER', 'Missing from books')
        blockers = api.draft_preview(kind='Opening', units=self.ref('VCL-RL-2026-00003'), posting_date='2026-11-16', rates={'PAPER': 1})['blockers']
        self.assertTrue(any('signed tagging sheet' in b for b in blockers))
        self.assertTrue(any('no measured weight' in b for b in blockers))
        self.assertEqual(self.inserted, [])

    def test_batched_opening_uses_the_tag_as_batch(self):
        api.set_item(self.ref('VCL-RL-2026-00001'), 'BATCHED', 'Missing from books')
        self.sheet('REG-1', {'VCL-RL-2026-00001': 'Present'})
        api.prepare_draft('REQ-1', 'Opening', self.ref('VCL-RL-2026-00001'), '2026-11-16', {'BATCHED': 100})
        row = self.inserted[-1].items[0]
        self.assertEqual((row['batch_no'], row['use_serial_batch_fields'], row['qty']), ('VCL-RL-2026-00001', 1, 500))
        self.assertIn(('Batch', 'VCL-RL-2026-00001'), self.docs)

    def test_transfer_moves_book_stock_to_where_it_physically_is(self):
        self.ready('In books')
        args = dict(kind='Transfer', units=self.ref('VCL-RL-2026-00001', 'VCL-RL-2026-00002'), posting_date='2026-11-16', source_warehouse='Stores')
        self.balances[('PAPER', 'Stores')] = 700
        self.assertTrue(any('holds 700' in b for b in api.draft_preview(**args)['blockers']))
        self.balances[('PAPER', 'Stores')] = 5000
        api.prepare_draft('REQ-T', **args)
        se = self.inserted[-1]
        self.assertEqual((se.doctype, se.purpose), ('Stock Entry', 'Material Transfer'))
        self.assertEqual([(r['item_code'], r['qty'], r['s_warehouse'], r['t_warehouse']) for r in se.items], [('PAPER', 800, 'Stores', 'Source')])
        # Opening is refused for stock marked as in the books.
        self.assertTrue(any('Missing from books' in b for b in api.draft_preview(kind='Opening', units=args['units'], posting_date='2026-11-16', rates={'PAPER': 1})['blockers']))

    def test_voucher_lifecycle_updates_units(self):
        self.ready()
        api.prepare_draft('REQ-1', 'Opening', self.ref('VCL-RL-2026-00001'), '2026-11-16', {'PAPER': 1})
        sr = Doc(doctype='Stock Reconciliation', name='MAT-RECO-1')
        api.on_voucher_change(sr, 'on_submit'); self.assertEqual(self.unit('VCL-RL-2026-00001').stock_draft_status, 'Submitted')
        api.on_voucher_change(sr, 'on_cancel'); self.assertEqual(self.unit('VCL-RL-2026-00001').stock_draft_status, 'Cancelled')
        api.on_voucher_change(sr, 'on_trash'); self.assertIsNone(self.unit('VCL-RL-2026-00001').stock_draft)

    def test_tagging_sheet_lists_every_unit_with_tick_boxes(self):
        with patch('vcl_procurement.print_brand.logo_uri', return_value='data:,'):
            html = api.tagging_sheet('REG-3')['html']
        self.assertIn('VCL-RL-2026-00003', html); self.assertIn('VCL-RL-2026-00004', html)
        self.assertIn('Not found', html); self.assertIn('Signature', html); self.assertIn('not weighed', html)


if __name__ == '__main__':
    unittest.main()
