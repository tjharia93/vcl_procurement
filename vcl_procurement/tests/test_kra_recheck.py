"""KRA daily re-check: pure rules and the daily job with frappe stubbed.  PYTHONPATH=. python3 -m unittest vcl_procurement.tests.test_kra_recheck"""
import sys
import types
import unittest
from unittest import mock

from vcl_procurement.tests.test_port_api import pi, frappe  # noqa: E402,F401  (sets up the frappe stub the other tests use)
from vcl_procurement.api import rules  # noqa: E402
from vcl_procurement.api import kra_recheck  # noqa: E402

DROPPED = {"valid": False, "error": "KRA did not answer within the time limit. The invoice will be re-checked daily.", "timed_out": True}
NOT_FOUND = {"valid": False, "error": "Invoice not found"}


class Rules(unittest.TestCase):
    def test_transient(self):
        self.assertTrue(rules.kra_is_transient(DROPPED))
        self.assertTrue(rules.kra_is_transient({"valid": False, "error": "KRA iTax unreachable: reset"}))
        self.assertTrue(rules.kra_is_transient({"valid": False, "error": "Non-JSON response from KRA iTax (status 200)"}))
        self.assertFalse(rules.kra_is_transient(NOT_FOUND))
        self.assertFalse(rules.kra_is_transient({"valid": True, "inv_date": "2026-11-01"}))
        self.assertFalse(rules.kra_is_transient(None))

    def test_outcome(self):
        self.assertEqual(rules.recheck_outcome(DROPPED, "2026-11-05"), "Pending")
        self.assertEqual(rules.recheck_outcome(NOT_FOUND, "2026-11-05"), "Failed")
        self.assertEqual(rules.recheck_outcome({"valid": True, "inv_date": "2026-11-01"}, "2026-11-05"), "Verified")   # same month
        self.assertEqual(rules.recheck_outcome({"valid": True, "inv_date": "2026-11-05"}, "2026-11-05"), "Verified")
        self.assertEqual(rules.recheck_outcome({"valid": True, "inv_date": "2026-09-30"}, "2026-11-05"), "Mismatch")   # cross month
        self.assertEqual(rules.recheck_outcome({"valid": True, "inv_date": "2026-11-09"}, "2026-11-05"), "Mismatch")   # bill date earlier
        self.assertEqual(rules.recheck_outcome({"valid": True}, "2026-11-05"), "Failed")                                # no KRA date

    def test_three_silent_mornings_report(self):
        self.assertEqual(rules.next_recheck_state("Pending", 0), ("Pending", 1, False))
        self.assertEqual(rules.next_recheck_state("Pending", 1), ("Pending", 2, False))
        self.assertEqual(rules.next_recheck_state("Pending", 2), ("Failed", 3, True))
        self.assertEqual(rules.next_recheck_state("Verified", 1), ("Verified", 1, False))
        self.assertEqual(rules.next_recheck_state("Mismatch", 0), ("Mismatch", 0, True))

    def test_default_recipients(self):
        self.assertEqual(rules.KRA_RECHECK_RECIPIENTS, ["account@vimit.com", "tanuj.haria@vimit.com"])
        self.assertEqual(rules.KRA_DEADLINE_SECONDS, 15)


class Job(unittest.TestCase):
    def setUp(self):
        self.sets = []
        self.mail = []
        fr = kra_recheck.frappe
        self._saved = {k: getattr(fr, k, None) for k in ("db", "get_all", "get_doc", "sendmail", "conf", "utils")}
        fr.db = types.SimpleNamespace(set_value=lambda dt, name, vals, update_modified=True: self.sets.append((name, dict(vals), update_modified)))
        fr.get_doc = lambda *a, **k: types.SimpleNamespace(add_comment=lambda *x, **y: None)
        fr.sendmail = lambda **k: self.mail.append(k)
        fr.conf = {}
        u = vars(fr.utils) if hasattr(fr, 'utils') and not isinstance(fr.utils, types.ModuleType) else {}
        fr.utils = types.SimpleNamespace(**{**u, 'escape_html': lambda s: s})

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(kra_recheck.frappe, k, v)

    def rows(self, *rs):
        kra_recheck.frappe.get_all = lambda *a, **k: list(rs)

    def run_job(self, answers):
        with mock.patch("vcl_procurement.api.pi._kra_lookup", side_effect=answers):
            return kra_recheck.run()

    def test_verified_clears_without_email(self):
        self.rows({"name": "PINV-1", "bill_no": "ABC123", "bill_date": "2026-11-05", "supplier": "S", "custom_kra_recheck_tries": 1})
        out = self.run_job([{"valid": True, "inv_date": "2026-11-02"}])
        self.assertEqual(out["Verified"], 1)
        self.assertEqual(self.sets[-1][1]["custom_kra_recheck"], "Verified")
        self.assertTrue(self.sets[-1][2] is False, "update_modified off: the check must not touch the invoice's modified time")
        self.assertEqual(self.mail, [])

    def test_silent_again_stays_pending_until_third_morning_then_emails(self):
        r = {"name": "PINV-2", "bill_no": "ABC124", "bill_date": "2026-11-05", "supplier": "S", "custom_kra_recheck_tries": 1}
        self.rows(r)
        self.run_job([DROPPED])
        self.assertEqual(self.sets[-1][1], {"custom_kra_recheck": "Pending", "custom_kra_recheck_tries": 2})
        self.assertEqual(self.mail, [])
        self.rows({**r, "custom_kra_recheck_tries": 2})
        self.run_job([DROPPED])
        self.assertEqual(self.sets[-1][1], {"custom_kra_recheck": "Failed", "custom_kra_recheck_tries": 3})
        self.assertEqual(len(self.mail), 1)
        self.assertEqual(self.mail[0]["recipients"], ["account@vimit.com", "tanuj.haria@vimit.com"])

    def test_site_config_recipients_override(self):
        kra_recheck.frappe.conf = {"kra_recheck_recipients": ["x@example.com"]}
        self.rows({"name": "PINV-3", "bill_no": "ABC125", "bill_date": "2026-11-05", "supplier": "S", "custom_kra_recheck_tries": 0})
        self.run_job([NOT_FOUND])
        self.assertEqual(self.mail[0]["recipients"], ["x@example.com"])

    def test_exempt_supplier_is_cleared_not_looked_up(self):
        self.rows({"name": "PINV-4", "bill_no": "", "bill_date": "2026-11-05", "supplier": "S", "custom_kra_recheck_tries": 0, "custom_kra_cuin_exempt": 1})
        with mock.patch("vcl_procurement.api.pi._kra_lookup", side_effect=AssertionError("must not be called")):
            kra_recheck.run()
        self.assertEqual(self.sets[-1][1]["custom_kra_recheck"], "Verified")

    def doc(self, **k):
        return types.SimpleNamespace(**{"name": "PINV-9", "docstatus": 1, "bill_date": "2026-11-05", "custom_kra_recheck": None,
                                        "custom_kra_recheck_tries": 0, **k}, get=lambda key, d=None: {"name": "PINV-9", "docstatus": 1, "bill_date": "2026-11-05", **k}.get(key, d))

    def test_a_silent_live_lookup_parks_the_invoice(self):
        self.assertEqual(kra_recheck.note_live_lookup(self.doc(), DROPPED), "Pending")
        self.assertEqual(self.sets[-1][1], {"custom_kra_recheck": "Pending", "custom_kra_recheck_tries": 0})

    def test_a_second_silent_lookup_does_not_reset_the_counter(self):
        self.assertEqual(kra_recheck.note_live_lookup(self.doc(custom_kra_recheck="Pending", custom_kra_recheck_tries=2), DROPPED), "Pending")
        self.assertEqual(self.sets, [])

    def test_a_parked_invoice_settles_when_the_live_lookup_answers(self):
        out = kra_recheck.note_live_lookup(self.doc(custom_kra_recheck="Pending", custom_kra_recheck_tries=1), {"valid": True, "inv_date": "2026-11-01"})
        self.assertEqual(out, "Verified")
        self.assertEqual(self.sets[-1][1]["custom_kra_recheck"], "Verified")

    def test_an_ordinary_answer_leaves_a_normal_invoice_alone(self):
        self.assertIsNone(kra_recheck.note_live_lookup(self.doc(), {"valid": True, "inv_date": "2026-11-01"}))
        self.assertEqual(self.sets, [])

    def test_a_cancelled_invoice_is_never_parked(self):
        self.assertIsNone(kra_recheck.note_live_lookup(self.doc(docstatus=2), DROPPED))


if __name__ == "__main__":
    unittest.main()
