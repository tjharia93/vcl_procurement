"""Run without a bench:  PYTHONPATH=. python3 vcl_procurement/tests/test_push_watch.py"""
import sys
import types
import unittest
from datetime import datetime

_f = types.ModuleType("frappe")
_f.cache = lambda: None
sys.modules.setdefault("frappe", _f)
from vcl_procurement.api.push_watch import stuck_rows  # noqa: E402

NOW = datetime(2026, 10, 13, 12, 0, 0)


class T(unittest.TestCase):
    def test_only_old_submitted_pending(self):
        rows = [
            {"name": "OLD", "docstatus": 1, "push_status": "Pending", "modified": "2026-10-13 11:00:00"},
            {"name": "NEW", "docstatus": 1, "push_status": "Pending", "modified": "2026-10-13 11:50:00"},
            {"name": "DONE", "docstatus": 1, "push_status": "Pushed", "modified": "2026-10-13 09:00:00"},
            {"name": "FAIL", "docstatus": 1, "push_status": "Failed", "modified": "2026-10-13 09:00:00"},
            {"name": "DRAFT", "docstatus": 0, "push_status": "Pending", "modified": "2026-10-13 09:00:00"},
        ]
        self.assertEqual([r["name"] for r in stuck_rows(rows, NOW)], ["OLD"])


if __name__ == "__main__":
    unittest.main()
