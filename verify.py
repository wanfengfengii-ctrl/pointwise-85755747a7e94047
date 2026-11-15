"""Existing regression and offline SDK compatibility checks."""
import copy
import unittest
from vendor import sdk1, sdk2
from samples import BUFFER, EXPECTED_B, EXPECTED_ROWS, EXPECTED_SUMMARY, memory

class LegacyRegression(unittest.TestCase):
    def setUp(self):
        self.db, self.archive, self.cohort = memory()
        self.addCleanup(self.db.close)

    def test_saved_analysis(self):
        self.assertEqual(self.archive.read("b"), EXPECTED_B)
        self.assertEqual(self.cohort.read("review"), EXPECTED_ROWS)
        self.assertEqual(self.cohort.summary("review"), EXPECTED_SUMMARY)
        self.assertEqual(self.cohort.read("empty"), [])
        self.assertEqual(self.archive.read("zero"), [])
        self.assertEqual(self.archive.read("unselected"), [{"station": "尾站", "samples": [100]}])
        self.assertEqual(self.db.execute("SELECT count(*) FROM buffers1").fetchone()[0], 1)

    def test_selection_validation(self):
        with self.assertRaises(ValueError):
            self.cohort.create("bad", [("b", 1)])
        with self.assertRaises(KeyError):
            self.cohort.create("bad", [("absent", 0)])
        self.assertIsNone(self.db.execute("SELECT 1 FROM cohorts1 WHERE name='bad'").fetchone())
        with self.assertRaises(ValueError):
            self.archive.append("b", BUFFER, 2, 2)
        self.cohort.create("repeat", [("b", 2), ("b", 2)])
        self.assertEqual(self.cohort.summary("repeat")["value_sum"], -4)

    def test_weak_legacy_types(self):
        altered = copy.deepcopy(BUFFER)
        altered["station"][2] = 31
        sdk1.check(altered, 2, 2)
        self.assertEqual(sdk1.row(altered, 2)["station"], 31)

    def test_sdk2_is_a_real_different_contract(self):
        with self.assertRaises(ValueError):
            sdk2.validate(BUFFER)
        sdk2.install(self.db)
        batch = {"valid": [True, False, True, True],
                 "station": ["甲", "", "乙", "丙"],
                 "list_valid": [True, False, False, True],
                 "offsets": [0, 2, 2, 2, 2], "values": [0, -2]}
        sdk2.put(self.db, "native", batch)
        self.db.commit()
        self.assertEqual(sdk2.row(sdk2.get(self.db, "native"), 0),
                         {"station": "甲", "samples": [0, -2]})
        self.assertIsNone(sdk2.row(batch, 1))
        self.assertEqual(sdk2.row(batch, 2)["samples"], None)
        self.assertEqual(sdk2.row(batch, 3)["samples"], [])
        for key, value in [("values", [False, -2]), ("offsets", [0, 2, 1, 2, 2]),
                           ("station", ["甲", 9, "乙", "丙"])]:
            changed = copy.deepcopy(batch)
            changed[key] = value
            with self.assertRaises(ValueError):
                sdk2.validate(changed)

if __name__ == "__main__":
    unittest.main()
