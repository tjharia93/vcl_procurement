"""Purchase Invoice change requests of 31/10/2026 (CR-010 date rule at push), with `frappe` stubbed.

    python3 -m unittest vcl_procurement.tests.test_cr_1031
"""
import datetime
import sys
import types
import unittest


class _Thrown(Exception):
    pass


def _throw(msg, exc=None, title=None):
    raise _Thrown(msg)


def _stub_frappe():
    """A stub `frappe` when none is loaded yet; other test modules may have loaded theirs first."""
    if "frappe" not in sys.modules:
        f = types.ModuleType("frappe")
        u = types.ModuleType("frappe.utils")
        u.flt = lambda v, *a: float(v or 0)
        u.nowdate = lambda: "2026-10-31"
        u.today = u.nowdate
        u.getdate = lambda v: v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)[:10])
        u.add_days = lambda d, n: d
        u.date_diff = lambda a, b: (u.getdate(a) - u.getdate(b)).days
        f.utils = u
        f._ = lambda s: s
        f.whitelist = lambda *a, **k: (lambda fn: fn)
        f.PermissionError = PermissionError
        sys.modules["frappe"] = f
        sys.modules["frappe.utils"] = u
    for m in ("erpnext", "erpnext.buying", "erpnext.buying.doctype", "erpnext.buying.doctype.purchase_order",
              "erpnext.buying.doctype.purchase_order.purchase_order"):
        sys.modules.setdefault(m, types.ModuleType(m))
    sys.path.insert(0, __file__.rsplit("/vcl_procurement/tests/", 1)[0])
    from vcl_procurement.api import pi
    return pi


class Doc(types.SimpleNamespace):
    def get(self, k, d=None):
        return getattr(self, k, d)

    def check_permission(self, *a):
        pass

    def add_comment(self, kind, text):
        self.comments.append(text)


def _setup(test, kra, itype="Local Purchase", bill_date="2026-11-01", exempt=0):
    doc = Doc(name="ACC-PINV-X", bill_no="0430146720000082785", bill_date=bill_date, custom_purchase_invoice_type=itype,
              custom_kra_cuin_exempt=exempt, comments=[])
    calls = []

    def validate_cuin(invoice_no):
        calls.append(invoice_no)
        if isinstance(kra, Exception):
            raise kra
        return kra
    pi = _stub_frappe()
    f = pi.frappe
    saved = {k: getattr(f, k, None) for k in ("get_doc", "get_attr", "get_all", "throw", "get_roles", "_", "session", "parse_json")}
    f.parse_json = lambda v: v
    f.get_doc = lambda dt, name: doc
    f.get_attr = lambda path: validate_cuin
    f.get_all = lambda *a, **k: []
    f.throw = _throw
    f._ = lambda s: s
    f.get_roles = lambda *a: ["Purchase User"]
    f.session = types.SimpleNamespace(user="t@x")
    page = pi.pi_page
    pi.pi_page = lambda name: "page"
    pi._ = lambda s: s

    def undo():
        pi.pi_page = page
        for k, v in saved.items():
            setattr(f, k, v)
    test.addCleanup(undo)
    return doc, calls, pi


class DateRuleAtPush(unittest.TestCase):
    def test_cross_month_is_refused_until_reviewed(self):
        doc, _, pi = _setup(self, {"valid": True, "inv_date": "31/10/2026"})
        with self.assertRaises(_Thrown) as e:
            pi.qbo_prepare("ACC-PINV-X")
        self.assertIn("Cross-month", str(e.exception))
        self.assertIn("31/10/2026", str(e.exception))
        self.assertEqual(pi.qbo_prepare("ACC-PINV-X", date_reviewed=1), "page")
        self.assertEqual(len(doc.comments), 1)
        self.assertIn("Bill date reviewed", doc.comments[0])

    def test_same_month_late_posting_goes_through_without_a_comment(self):
        doc, _, pi = _setup(self, {"valid": True, "inv_date": "2026-10-27"}, bill_date="2026-10-31")
        self.assertEqual(pi.qbo_prepare("ACC-PINV-X"), "page")
        self.assertEqual(doc.comments, [])

    def test_kra_down_leaves_it_unverified_and_does_not_block(self):
        _, _, pi = _setup(self, RuntimeError("iTax unreachable"))
        self.assertEqual(pi.qbo_prepare("ACC-PINV-X"), "page")
        _, _, pi = _setup(self, {"valid": False, "error": "timed out"})
        self.assertEqual(pi.qbo_prepare("ACC-PINV-X"), "page")

    def test_an_import_is_not_asked_of_kra(self):
        _, calls, pi = _setup(self, {"valid": True, "inv_date": "2026-01-01"}, itype="Importation")
        self.assertEqual(pi.qbo_prepare("ACC-PINV-X"), "page")
        self.assertEqual(calls, [])


class KraCheck(unittest.TestCase):
    def test_one_call_gives_kra_and_the_verdict(self):
        _, calls, pi = _setup(self, {"valid": True, "inv_date": "27/10/2026", "tax_amt": "14860.80"}, bill_date="2026-10-31")
        r = pi.kra_check("ACC-PINV-X")
        self.assertEqual(calls, ["0430146720000082785"])
        self.assertEqual(r["kra"]["tax_amt"], "14860.80")
        self.assertEqual((r["date"]["state"], r["date"]["ok"]), ("same_month", True))

    def test_exempt_supplier_is_not_asked(self):
        _, calls, pi = _setup(self, {"valid": True, "inv_date": "2026-10-27"}, exempt=1)
        r = pi.kra_check("ACC-PINV-X")
        self.assertEqual((calls, r["kra"], r["date"]["state"]), ([], None, "unverified"))


if __name__ == "__main__":
    unittest.main()


class Item(types.SimpleNamespace):
    def get(self, k, d=None):
        return getattr(self, k, d)

    def update(self, d):
        for k, v in d.items():
            setattr(self, k, v)


class Bill(types.SimpleNamespace):
    def get(self, k, d=None):
        return getattr(self, k, d)

    def set(self, k, v):
        setattr(self, k, v)


def _bill():
    return Bill(custom_purchase_invoice_type="Local Purchase", posting_date="2026-10-31", update_stock=0, items=[
        Item(po_detail="P1", item_name="Tape", item_code="T", qty=2160.0, rate=43.0, idx=1),
        Item(po_detail="P2", item_name="Box", item_code="B", qty=10.0, rate=5.0, idx=2)])


class Workspace(unittest.TestCase):
    def setUp(self):
        _, _, self.pi = _setup(self, {"valid": False})

    def test_lines_are_trimmed_re_priced_and_numbered(self):
        b = self.pi._shape_bill(_bill(), {"lines": [{"po_detail": "P1", "qty": 1000, "rate": 42.5}, {"po_detail": "P2", "include": False}],
                                          "supplier_ref": " DN-7 ", "update_stock": 1, "posting_date": "2026-10-30"})
        self.assertEqual([(i.po_detail, i.qty, i.rate, i.idx) for i in b.items], [("P1", 1000, 42.5, 1)])
        self.assertEqual((b.custom_supplier_reference_no, b.update_stock, b.posting_date, b.set_posting_time), ("DN-7", 1, "2026-10-30", 1))

    def test_refusals(self):
        for lines, word in (([{"po_detail": "P1", "qty": 0}], "more than zero"), ([{"po_detail": "P1", "qty": 3000}], "left to bill"),
                            ([{"po_detail": "P1", "include": False}, {"po_detail": "P2", "include": False}], "at least one line")):
            with self.assertRaises(_Thrown) as e:
                self.pi._shape_bill(_bill(), {"lines": lines})
            self.assertIn(word, str(e.exception))

    def test_same_supplier_invoice_no_returns_the_existing_draft(self):
        pi = self.pi
        saved = pi._draft_bills, pi.pi_page, pi._mapped_bill
        pi._draft_bills = lambda po: [{"name": "ACC-PINV-1", "bill_no": "0430146720000082785"}]
        pi.pi_page = lambda n: {"name": n}
        self.addCleanup(lambda: (setattr(pi, "_draft_bills", saved[0]), setattr(pi, "pi_page", saved[1]), setattr(pi, "_mapped_bill", saved[2])))
        pi._mapped_bill = lambda *a, **k: self.fail("must not create a second bill")
        r = pi.bill_save("PO-1", {"bill_no": " 0430146720000082785 ", "bill_date": "2026-10-27"})
        self.assertEqual((r["name"], r["existing"]), ("ACC-PINV-1", True))


class PurchasingSearch(unittest.TestCase):
    def test_finds_by_item_description_names_the_type_and_narrows_by_kind(self):
        pi = _setup(self, {"valid": False})[2]
        for m in ("frappe.query_builder",):
            if m not in sys.modules:
                mod = types.ModuleType(m)
                mod.DocType = lambda *a: None
                mod.resolve_factor = lambda *a, **k: None
                sys.modules[m] = mod
        from vcl_procurement.api import search
        f = search.frappe
        heads = {"Purchase Order": [Item(name="PUR-ORD-2026-00264", supplier="ROK INDUSTRIES LIMITED", status="To Receive and Bill",
                                         transaction_date="2026-10-22", creation="2026-10-22 09:00", docstatus=1)],
                 "Purchase Invoice": [Item(name="ACC-PINV-2026-00673", supplier_name="ROK INDUSTRIES LIMITED", status="Unpaid",
                                           posting_date="2026-10-31", bill_no="0430146720000082785", creation="2026-10-31 10:00", docstatus=1)]}
        lines = {"Purchase Order Item": [Item(parent="PUR-ORD-2026-00264", item_code="TAPE", item_name="Printed Cello Tape",
                                             description="<p>Cello tape (Cereal Box Ltd)</p>")]}
        saved = {k: getattr(f, k, None) for k in ("get_all", "get_meta")}
        f.get_all = lambda dt, **k: heads.get(dt, []) if "Item" not in dt else lines.get(dt, [])
        f.get_meta = lambda dt: types.SimpleNamespace(has_field=lambda fn: True)
        self.addCleanup(lambda: [setattr(f, k, v) for k, v in saved.items()])
        utils = sys.modules["frappe.utils"]
        if not hasattr(utils, "strip_html"):
            utils.strip_html = lambda s: __import__("re").sub("<[^>]+>", " ", s)
            self.addCleanup(lambda: delattr(utils, "strip_html"))
        r = search.purchasing_search("cereal box")
        self.assertEqual([(x["type"], x["name"]) for x in r], [("Purchase Order", "PUR-ORD-2026-00264")])
        self.assertEqual(r[0]["href"], "/compass/purchasing/po/PUR-ORD-2026-00264")
        self.assertEqual([x["name"] for x in search.purchasing_search("rok", kind="pi")], ["ACC-PINV-2026-00673"])
        self.assertEqual(search.purchasing_search("x"), [])


class CancelInCompass(unittest.TestCase):
    def _doc(self, docstatus=1):
        d = Doc(name="ACC-PINV-X", docstatus=docstatus, comments=[], cancelled=False)
        d.cancel = lambda: setattr(d, "cancelled", True)
        return d

    def _go(self, doc, check, **kw):
        _, _, pi = _setup(self, {"valid": False})
        pi.frappe.get_doc = lambda dt, n: doc
        saved = pi._cancel_state
        pi._cancel_state = lambda d: (check, {"qbo_bill_id": "1234" if check.get("needs_qbo_ack") else None})
        self.addCleanup(lambda: setattr(pi, "_cancel_state", saved))
        return pi.pi_cancel("ACC-PINV-X", **kw)

    def test_needs_a_reason_and_no_blocker(self):
        with self.assertRaises(_Thrown):
            self._go(self._doc(), {"blockers": [], "warnings": [], "needs_qbo_ack": False}, reason="no")
        with self.assertRaises(_Thrown) as e:
            self._go(self._doc(), {"blockers": ["Posted payments against it (ACC-PAY-1)"], "warnings": [], "needs_qbo_ack": False}, reason="wrong supplier")
        self.assertIn("ACC-PAY-1", str(e.exception))

    def test_a_synced_bill_needs_the_qbo_tick(self):
        chk = {"blockers": [], "warnings": [], "needs_qbo_ack": True}
        with self.assertRaises(_Thrown) as e:
            self._go(self._doc(), chk, reason="wrong supplier")
        self.assertIn("1234", str(e.exception))
        d = self._doc()
        self._go(d, chk, reason="wrong supplier", qbo_ack=1)
        self.assertTrue(d.cancelled)
        self.assertIn("1234", d.comments[0])

    def test_cancels_with_the_reason_recorded_and_a_repeat_changes_nothing(self):
        d = self._doc()
        r = self._go(d, {"blockers": [], "warnings": [], "needs_qbo_ack": False}, reason="Supplier re-issued the invoice")
        self.assertEqual((d.cancelled, r["already"]), (True, False))
        self.assertIn("Supplier re-issued the invoice", d.comments[0])
        done = self._doc(docstatus=2)
        self.assertTrue(self._go(done, {"blockers": [], "warnings": [], "needs_qbo_ack": False}, reason="x")["already"])
        self.assertFalse(done.cancelled)
