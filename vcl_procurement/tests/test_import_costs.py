"""The wiring of the import costs API, with frappe faked: the switch, the roles, what a customs
save writes to the Journal Entry and the invoice, and the receiving lock. The real documents are
checked on the site after deploy.

    python3 -m unittest vcl_procurement.tests.test_import_costs
"""
import datetime
import json
import sys
import types
import unittest

frappe = sys.modules.get("frappe") or types.ModuleType("frappe")


class _Thrown(Exception):
    pass


def _throw(msg, exc=None, title=None):
    raise _Thrown(msg)


frappe.whitelist = lambda *a, **k: (lambda f: f)
frappe.throw = _throw
frappe._ = lambda s: s
frappe.PermissionError = PermissionError
utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")
utils.flt = lambda v, *a: float(v or 0)
utils.nowdate = lambda: datetime.date.today().isoformat()
utils.today = utils.nowdate
utils.date_diff = lambda a, b: 0
utils.getdate = lambda v: datetime.date.fromisoformat(str(v)[:10])
utils.add_days = lambda d, n: d
frappe.utils = utils
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", utils)

sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
from vcl_procurement.api import import_costs as C  # noqa: E402


class Row(dict):
    __getattr__ = dict.get

    def as_dict(self):
        return dict(self)


class Doc(Row):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.saved = self.submitted = 0

    def check_permission(self, perm=None):
        pass

    def update(self, d):
        dict.update(self, d)

    def set(self, k, v):
        self[k] = v

    def calculate_taxes_and_totals(self):
        pass

    def save(self):
        self.saved += 1
        self.setdefault("name", "ACC-JV-2026-00001")

    insert = save

    def submit(self):
        self.submitted += 1
        self["docstatus"] = 1


class Meta:
    def has_field(self, f):
        return True


def invoice(**kw):
    base = dict(name="ACC-PINV-2026-00700", docstatus=0, is_return=0, custom_purchase_invoice_type="Importation",
                currency="USD", conversion_rate=129.0, company="VCL", bill_no="INV-88213", total_taxes_and_charges=0,
                grand_total=27000, rounded_total=27000, net_total=27000, items=[],
                custom_container_details_table=[Row(container_reference="MSKU4182730")],
                payment_schedule=[Row(payment_term="120 days", due_date="2027-03-01", invoice_portion=100, payment_amount=27000)])
    base.update(kw)
    return Doc(**base)


PAYLOAD = json.dumps({"entry_no": "26MBAIM401234567", "entry_date": "2026-11-20", "rate": "129.45", "duty": "349,515",
                      "idf": "87379", "rdl": "69903", "vat": "615146", "paid_from": "KCB - VCL"})


class Site:
    """Just enough of frappe for one call."""

    def __init__(self, pi, switch=None, roles=("Accounts Manager",), post_for=None, entries=None):
        self.pi, self.je, self.entries = pi, None, entries or []
        frappe.conf = {"vcl_import_customs_entry": switch, "vcl_import_vat_account": "VAT IN - VCL",
                       "vcl_import_post_for": post_for or []}
        frappe.get_roles = lambda *a: list(roles)
        frappe.get_meta = lambda dt: Meta()
        frappe.parse_json = lambda v: json.loads(v) if isinstance(v, str) else v
        frappe.get_doc = lambda dt, name=None: self.pi if dt == C.PI else self.je
        frappe.new_doc = self.new_doc
        frappe.get_cached_value = lambda dt, name, field: {"expenses_included_in_valuation": "EIV - VCL",
                                                           "cost_center": "Main - VCL"}[field]
        frappe.get_all = self.get_all
        frappe.clear_messages = lambda: None
        frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None, savepoint=lambda n: None,
                                          rollback=lambda **k: None, exists=lambda *a, **k: False)

    def new_doc(self, dt):
        self.je = Doc(doctype=dt)
        return self.je

    def get_all(self, dt, filters=None, fields=None, pluck=None, **kw):
        if dt == "Account":
            return [Row(name="KCB - VCL")]
        if dt == C.JE and (filters or {}).get("custom_import_entry_kind"):
            return self.entries
        return []


class CustomsSave(unittest.TestCase):
    def setUp(self):
        self._page = C.import_page
        C.import_page = lambda pi: {"name": pi}

    def tearDown(self):
        C.import_page = self._page

    def test_switched_off_refuses(self):
        Site(invoice())
        with self.assertRaises(_Thrown) as cm:
            C.customs_save("ACC-PINV-2026-00700", PAYLOAD)
        self.assertIn("switched off", str(cm.exception))

    def test_purchasing_user_cannot_post_costs(self):
        Site(invoice(), switch="draft", roles=("Purchase User",))
        with self.assertRaises(_Thrown) as cm:
            C.customs_save("ACC-PINV-2026-00700", PAYLOAD)
        self.assertIn("Only Finance", str(cm.exception))

    def test_saves_draft_entry_and_rerates_the_draft_invoice(self):
        site = Site(invoice(), switch="draft")
        C.customs_save("ACC-PINV-2026-00700", PAYLOAD)
        je, pi = site.je, site.pi
        self.assertEqual((je.saved, je.submitted, pi.submitted), (1, 0, 0))
        self.assertEqual(je["custom_import_invoice"], "ACC-PINV-2026-00700")
        self.assertEqual(je["custom_import_entry_kind"], "Customs entry")
        self.assertEqual(je["posting_date"], "2026-11-20")
        rows = je["accounts"]
        self.assertEqual(round(sum(r["debit_in_account_currency"] for r in rows), 2), 1121943.0)
        self.assertEqual(rows[-1]["credit_in_account_currency"], 1121943.0)
        self.assertEqual(rows[0]["account"], "VAT IN - VCL")
        self.assertTrue(all(r["cost_center"] == "Main - VCL" for r in rows))
        self.assertEqual(pi["conversion_rate"], 129.45)
        self.assertEqual(pi["custom_customs_exchange_rate"], 129.45)
        self.assertEqual(pi["custom_kra_import_number"], "26MBAIM401234567")
        self.assertEqual(pi["posting_date"], "2026-11-20")
        self.assertEqual(pi["custom_customs_journal_entry"], je["name"])

    def test_taxes_still_on_the_invoice_refuse(self):
        Site(invoice(total_taxes_and_charges=8667), switch="draft")
        with self.assertRaises(_Thrown) as cm:
            C.customs_save("ACC-PINV-2026-00700", PAYLOAD)
        self.assertIn("booked twice", str(cm.exception))

    def test_slipped_rate_needs_confirming(self):
        bad = json.dumps({**json.loads(PAYLOAD), "rate": "12.945"})
        Site(invoice(), switch="draft")
        with self.assertRaises(_Thrown):
            C.customs_save("ACC-PINV-2026-00700", bad)
        site = Site(invoice(), switch="draft")
        C.customs_save("ACC-PINV-2026-00700", bad, confirm_rate=1)
        self.assertEqual(site.pi["conversion_rate"], 12.945)

    def test_submitted_invoice_refuses(self):
        Site(invoice(docstatus=1), switch=1)
        with self.assertRaises(_Thrown):
            C.customs_save("ACC-PINV-2026-00700", PAYLOAD)

    def test_local_invoice_refuses(self):
        Site(invoice(custom_purchase_invoice_type="Local Purchase"), switch=1)
        with self.assertRaises(_Thrown) as cm:
            C.customs_save("ACC-PINV-2026-00700", PAYLOAD)
        self.assertIn("not an importation invoice", str(cm.exception))

    def test_unknown_bank_account_refuses(self):
        Site(invoice(), switch="draft")
        with self.assertRaises(_Thrown):
            C.customs_save("ACC-PINV-2026-00700", json.dumps({**json.loads(PAYLOAD), "paid_from": "Somewhere"}))


class CustomsPost(unittest.TestCase):
    def test_draft_site_does_not_post(self):
        Site(invoice(), switch="draft")
        with self.assertRaises(_Thrown) as cm:
            C.customs_post("ACC-PINV-2026-00700")
        self.assertIn("drafts for now", str(cm.exception))

    def test_needs_a_saved_entry(self):
        Site(invoice(), switch=1)
        with self.assertRaises(_Thrown) as cm:
            C.customs_post("ACC-PINV-2026-00700")
        self.assertIn("Save the customs entry", str(cm.exception))

    def test_rate_changed_after_save_refuses(self):
        Site(invoice(custom_customs_exchange_rate=129.45), switch=1, entries=[Row(name="JV-1", docstatus=0)])
        with self.assertRaises(_Thrown) as cm:
            C.customs_post("ACC-PINV-2026-00700")
        self.assertIn("rate was changed", str(cm.exception))


class Lock(unittest.TestCase):
    def test_site_not_posting_never_reads_the_order(self):
        Site(invoice(), switch="draft")
        frappe.db.get_value = lambda *a, **k: (_ for _ in ()).throw(AssertionError("read"))
        self.assertEqual(C.receiving_lock("PUR-ORD-2026-00300"), "")

    def test_import_order_without_invoice_is_locked(self):
        Site(invoice(), switch=1)
        frappe.db.get_value = lambda *a, **k: "Import"
        self.assertIn("no supplier invoice", C.receiving_lock("PUR-ORD-2026-00300"))
        with self.assertRaises(_Thrown):
            C.assert_can_receive("PUR-ORD-2026-00300")

    def test_local_order_is_open(self):
        Site(invoice(), switch=1)
        frappe.db.get_value = lambda *a, **k: "Local"
        self.assertEqual(C.receiving_lock("PUR-ORD-2026-00301"), "")


if __name__ == "__main__":
    unittest.main()
