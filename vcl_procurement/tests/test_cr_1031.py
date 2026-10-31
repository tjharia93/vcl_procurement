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
    saved = {k: getattr(f, k, None) for k in ("get_doc", "get_attr", "get_all", "throw", "get_roles", "_", "session")}
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
