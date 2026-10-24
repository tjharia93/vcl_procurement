import unittest

from vcl_procurement.api import mr_rules as R


class StageTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual(R.stage({"docstatus": 0}), "Draft")
        self.assertEqual(R.stage({"docstatus": 1, "per_ordered": 0}), "To order")
        self.assertEqual(R.stage({"docstatus": 1, "per_ordered": 40}), "Partly ordered")
        self.assertEqual(R.stage({"docstatus": 1, "per_ordered": 100}), "Ordered")
        self.assertEqual(R.stage({"docstatus": 1, "per_ordered": 100, "per_received": 30}), "Partly received")
        self.assertEqual(R.stage({"docstatus": 1, "per_ordered": 100, "per_received": 100}), "Received")
        self.assertEqual(R.stage({"docstatus": 1, "status": "Stopped"}), "Stopped")
        self.assertEqual(R.stage({"docstatus": 2}), "Stopped")

    def test_steps_and_open(self):
        self.assertEqual(R.step_index("To order"), 0)
        self.assertEqual(R.step_index("Ordered"), 1)
        self.assertEqual(R.step_index("Received"), 2)
        self.assertEqual(R.step_index("Stopped"), -1)
        self.assertTrue(R.is_open("Partly received"))
        self.assertFalse(R.is_open("Received"))


class LineTests(unittest.TestCase):
    def test_lines(self):
        ok, problem = R.clean_lines([{"item_code": "RM-1", "qty": "5", "uom": "Kg"}])
        self.assertIsNone(problem)
        self.assertEqual(ok[0]["qty"], 5.0)
        self.assertEqual(R.clean_lines([])[1], "Add at least one line.")
        self.assertEqual(R.clean_lines([{"item_code": "", "qty": 1}])[1], "Line 1 names no item.")
        self.assertEqual(R.clean_lines([{"item_code": "X", "qty": 0}])[1], "Line 1: quantity must be above zero.")
        self.assertEqual(R.clean_lines([{"item_code": "X", "qty": "lots"}])[1], "Line 1: quantity is not a number.")


if __name__ == "__main__":
    unittest.main()
