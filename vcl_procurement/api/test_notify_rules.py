import unittest

from vcl_procurement.api import notify_rules as R


class NotifyRuleTests(unittest.TestCase):
    def test_who(self):
        self.assertEqual(R.who("jane.doe@vimit.com"), "Jane Doe")
        self.assertEqual(R.who(""), "Someone")

    def test_items(self):
        items = [{"item_name": "Kraft 125", "qty": 2000, "uom": "Kg"},
                 {"item_name": "Glue", "qty": 1.5, "uom": "Ltr"},
                 {"item_name": "Ink", "qty": 4, "uom": "Kg"}]
        self.assertEqual(R.first_items(items), "Kraft 125 2,000 Kg, Glue 1.50 Ltr and 1 more")
        self.assertEqual(R.first_items([]), "")

    def test_mr_events(self):
        row = {"name": "MAT-MR-1", "owner": "joan.k@vimit.com", "schedule_date": "2026-10-30",
               "at": "2026-10-24 10:00:00"}
        e = R.mr_event(row, [{"item_name": "Kraft", "qty": 5, "uom": "Kg"}], "mr_raised")
        self.assertEqual(e["id"], "mr_raised:MAT-MR-1")
        self.assertEqual(e["title"], "New material request MAT-MR-1")
        self.assertEqual(e["body"], "Joan K needs Kraft 5 Kg by 2026-10-30")
        s = R.mr_event(row, [], "mr_submitted")
        self.assertEqual(s["title"], "Material request MAT-MR-1 is ready to order")

    def test_dedupe_and_order(self):
        a = {"id": "x", "at": "2026-10-24 09:00:00"}
        b = {"id": "y", "at": "2026-10-24 10:00:00"}
        self.assertEqual([e["id"] for e in R.newest_first([a, b, dict(a)])], ["y", "x"])


if __name__ == "__main__":
    unittest.main()
