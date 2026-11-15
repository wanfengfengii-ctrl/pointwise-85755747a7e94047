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

# ---------------------------------------------------------------------------
# Real v1 -> v2 upgrade checks.  Everything below decodes logical rows with
# its own spec-derived helpers and never calls the migration converter.
# ---------------------------------------------------------------------------
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from unittest import mock

import migration
from archive import Archive
from cohort import Cohort
from samples import MEMBERS

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
LEGACY_TABLES = ("buffers1", "batches1", "cohorts1", "members1")
TARGET_TABLES = ("batches2", "slices2", "cohorts2", "members2")
SLICES = {"a": (1, 4), "b": (2, 2), "tail": (4, 3), "zero": (8, 0), "unselected": (7, 1)}
CANONICAL = {
    "a": {"valid": [False, True, True, True], "station": ["", "北站", "南站", "空列"],
          "list_valid": [False, True, True, True], "offsets": [0, 0, 2, 3, 3],
          "values": [0, -2, 7]},
    "b": {"valid": [True, True], "station": ["北站", "南站"],
          "list_valid": [True, True], "offsets": [0, 2, 3], "values": [0, -2, 7]},
    "tail": {"valid": [True, True, False], "station": ["空列", "缺列", ""],
             "list_valid": [True, False, False], "offsets": [0, 0, 0, 0], "values": []},
    "zero": {"valid": [], "station": [], "list_valid": [], "offsets": [0], "values": []},
    "unselected": {"valid": [True], "station": ["尾站"], "list_valid": [True],
                   "offsets": [0, 1], "values": [100]},
}
NATIVE = {"valid": [True, False, True, True], "station": ["甲", "", "乙", "丙"],
          "list_valid": [True, False, False, True], "offsets": [0, 2, 2, 2, 2],
          "values": [0, -2]}
# Hidden junk only: station 9 sits under a null struct, "junk"/None are not
# covered by any reachable span.  Reachable values are clean and include 0.
EXTRA_BUFFER = {"valid": [True, False, True], "station": ["甲", 9, "乙"],
                "list_valid": [True, True, False], "offsets": [0, 2, 2, 4],
                "values": [0, -1, "junk", None]}
EXTRA_CANONICAL = {"valid": [True, False, True], "station": ["甲", "", "乙"],
                   "list_valid": [True, False, False], "offsets": [0, 2, 2, 2],
                   "values": [0, -1]}


def spec_rows(buffer, offset, length):
    """Independent logical-row decode, written straight from the contract."""
    rows = []
    for i in range(offset, offset + length):
        if not buffer["valid"][i]:
            rows.append(None)
            continue
        samples = None
        if buffer["list_valid"][i]:
            a, b = buffer["offsets"][i:i + 2]
            samples = buffer["values"][a:b]
        rows.append({"station": buffer["station"][i], "samples": samples})
    return rows


def spec_summary(rows):
    result = {"members": len(rows), "null_structs": 0, "null_lists": 0,
              "empty_lists": 0, "value_count": 0, "value_sum": 0}
    for row in rows:
        if row is None:
            result["null_structs"] += 1
        elif row["samples"] is None:
            result["null_lists"] += 1
        else:
            result["empty_lists"] += not row["samples"]
            result["value_count"] += len(row["samples"])
            result["value_sum"] += sum(row["samples"])
    return result


def spec_cohort(members):
    by_batch = {bid: spec_rows(BUFFER, *slice_) for bid, slice_ in SLICES.items()}
    return [by_batch[bid][row - SLICES[bid][0]] for bid, row in members]


def audit_dump(db):
    return tuple(db.execute("SELECT * FROM %s ORDER BY %s" % spec).fetchall()
                 for spec in [("buffers1", "buffer_id"), ("batches1", "batch_id"),
                              ("cohorts1", "name"), ("members1", "name,pos")])


def target_tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            } & set(TARGET_TABLES)


class UpgradeRegression(unittest.TestCase):
    def setUp(self):
        self.db, self.archive, self.cohort = memory()
        self.addCleanup(self.db.close)

    def test_real_upgrade_independent_crosscheck(self):
        before = audit_dump(self.db)
        staged = []

        def checkpoint():
            # All target state staged (version flip included), not yet committed.
            staged.append(self.db.execute("SELECT count(*) FROM batches2").fetchone()[0])
            self.assertEqual(migration.storage_version(self.db), "2")

        result = migration.upgrade(self.db, before_commit=checkpoint)
        self.assertIsInstance(result, int)
        self.assertEqual(result, 2)
        self.assertEqual(staged, [len(SLICES)])
        # meta carries only the flipped version marker.
        self.assertEqual(dict(self.db.execute("SELECT key,value FROM meta")),
                         {"storage_version": "2"})
        # The four legacy tables are preserved byte-for-byte for audit.
        self.assertEqual(audit_dump(self.db), before)
        # Every batch: original slice coordinates and independent row decode.
        for bid, (start, count) in SLICES.items():
            expect = spec_rows(BUFFER, start, count)
            self.assertEqual(self.archive.bounds(bid), (start, count))
            self.assertEqual(self.archive.read(bid), expect)
            for j, row in enumerate(expect):
                self.assertEqual(self.archive.at(bid, start + j), row)
        # Target physical batches are exactly the expected canonical arrays.
        for bid, expect in CANONICAL.items():
            payload = json.loads(self.db.execute(
                "SELECT payload FROM batches2 WHERE batch_id=?", (bid,)).fetchone()[0])
            self.assertEqual(payload, expect)
            sdk2.validate(payload)
            self.assertEqual(sdk2.get(self.db, bid), expect)
        # Slice bindings and cohort bindings in the target tables.
        self.assertEqual(dict(self.db.execute("SELECT batch_id,start_row||','||row_count FROM slices2")),
                         {bid: "%d,%d" % s for bid, s in SLICES.items()})
        self.assertEqual(self.db.execute("SELECT name FROM cohorts2 ORDER BY name").fetchall(),
                         self.db.execute("SELECT name FROM cohorts1 ORDER BY name").fetchall())
        self.assertEqual(self.db.execute("SELECT * FROM members2 ORDER BY name,pos").fetchall(),
                         self.db.execute("SELECT * FROM members1 ORDER BY name,pos").fetchall())
        # Every cohort: identity, order, duplicates and the six counters.
        for name in ("review", "empty"):
            members = [(bid, row) for _, _, bid, row in self.db.execute(
                "SELECT * FROM members1 WHERE name=? ORDER BY pos", (name,))]
            self.assertEqual(self.cohort.read(name), spec_cohort(members))
            self.assertEqual(self.cohort.summary(name), spec_summary(spec_cohort(members)))
        self.assertEqual(self.cohort.read("review"), EXPECTED_ROWS)
        self.assertEqual(self.cohort.summary("review"), EXPECTED_SUMMARY)

    def test_null_empty_and_zero_stay_distinct(self):
        migration.upgrade(self.db)
        rows = self.archive.read("tail")
        self.assertEqual(rows[0], {"station": "空列", "samples": []})   # empty list
        self.assertEqual(rows[1], {"station": "缺列", "samples": None})  # null list
        self.assertIsNone(rows[2])                                       # null struct
        self.assertEqual(self.archive.read("zero"), [])                  # empty slice
        self.assertEqual(sdk2.get(self.db, "zero"), CANONICAL["zero"])
        self.assertEqual(self.archive.at("b", 2)["samples"], [0, -2])    # zero value kept
        tail = sdk2.get(self.db, "tail")
        self.assertEqual(tail["valid"], [True, True, False])
        self.assertEqual(tail["list_valid"], [True, False, False])

    def test_hidden_bad_values_do_not_block_upgrade(self):
        # BUFFER hides a non-string station, non-int values and negative
        # offsets outside every reachable span; upgrade must not reject them.
        self.assertEqual(migration.upgrade(self.db), 2)
        for bid, expect in CANONICAL.items():
            self.assertEqual(sdk2.get(self.db, bid), expect)

    def test_reachable_bad_value_rejects_whole_upgrade(self):
        db, archive, cohort = memory(bad=True)  # reachable station 31
        self.addCleanup(db.close)
        before = audit_dump(db)
        with self.assertRaises(ValueError):
            migration.upgrade(db)
        self.assertEqual(migration.storage_version(db), "1")
        self.assertEqual(target_tables(db), set())
        self.assertEqual(audit_dump(db), before)
        with self.assertRaises(ValueError):
            migration.upgrade(db, before_commit=lambda: None)

    def test_before_commit_exception_rolls_back(self):
        before = audit_dump(self.db)

        def boom():
            raise RuntimeError("stop")

        with self.assertRaises(RuntimeError):
            migration.upgrade(self.db, before_commit=boom)
        self.assertEqual(migration.storage_version(self.db), "1")
        self.assertEqual(target_tables(db := self.db), set())
        self.assertEqual(audit_dump(db), before)
        self.assertEqual(self.archive.read("b"), EXPECTED_B)  # legacy path intact
        self.assertEqual(migration.upgrade(self.db), 2)       # retry succeeds
        self.assertEqual(self.archive.read("b"), EXPECTED_B)

    def test_repeat_upgrade_is_a_noop(self):
        self.assertEqual(migration.upgrade(self.db), 2)
        snapshot = {t: self.db.execute("SELECT * FROM %s" % t).fetchall()
                    for t in TARGET_TABLES + ("meta",)}
        calls = []
        self.assertEqual(migration.upgrade(self.db, before_commit=lambda: calls.append(1)), 2)
        self.assertEqual(calls, [])  # no callback once already at version 2
        for table, rows in snapshot.items():
            self.assertEqual(self.db.execute("SELECT * FROM %s" % table).fetchall(), rows)

    def test_existing_objects_switch_without_reconstruction(self):
        self.assertEqual(self.archive.read("b"), EXPECTED_B)
        migration.upgrade(self.db)
        self.assertEqual(self.archive.read("b"), EXPECTED_B)
        self.assertEqual(self.cohort.summary("review"), EXPECTED_SUMMARY)
        self.archive.append("extra", EXTRA_BUFFER, 0, 3)
        self.cohort.create("late", [("extra", 0), ("b", 3)])
        self.assertEqual(self.cohort.summary("late")["value_sum"], 6)

    def test_post_switch_uses_only_sdk2_and_target_tables(self):
        with mock.patch.object(sdk1, "check", side_effect=AssertionError("sdk1 check")), \
             mock.patch.object(sdk1, "row", side_effect=AssertionError("sdk1 row")):
            self.assertEqual(migration.upgrade(self.db), 2)  # converter never calls SDK1

            def deny_legacy(action, arg1, arg2, dbname, source):
                if action == sqlite3.SQLITE_READ and arg1 in LEGACY_TABLES:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            self.db.set_authorizer(deny_legacy)
            try:
                self.assertEqual(self.archive.read("b"), EXPECTED_B)
                self.assertEqual(self.cohort.read("review"), EXPECTED_ROWS)
                self.assertEqual(self.cohort.summary("review"), EXPECTED_SUMMARY)
                self.archive.append("extra", EXTRA_BUFFER, 0, 3)
                self.cohort.create("late", [("extra", 0), ("b", 3)])
                self.assertEqual(self.cohort.read("late"),
                                 [{"station": "甲", "samples": [0, -1]}, EXPECTED_B[1]])
            finally:
                self.db.set_authorizer(None)

    def test_coordinate_validation_after_upgrade(self):
        migration.upgrade(self.db)
        with self.assertRaises(KeyError):
            self.archive.bounds("absent")
        with self.assertRaises(KeyError):
            self.archive.at("absent", 0)
        for bad_row in (1, 4, True, "2"):
            with self.assertRaises(ValueError):
                self.archive.at("b", bad_row)
        with self.assertRaises(ValueError):
            self.cohort.create("bad", [("b", 9)])
        with self.assertRaises(KeyError):
            self.cohort.create("bad", [("absent", 0)])
        self.assertIsNone(self.db.execute("SELECT 1 FROM cohorts2 WHERE name='bad'").fetchone())
        self.cohort.create("repeat", [("b", 2), ("b", 2)])
        self.assertEqual(self.cohort.summary("repeat")["value_sum"], -4)
        with self.assertRaises(ValueError):
            self.cohort.create("repeat", [])
        with self.assertRaises(KeyError):
            self.cohort.read("absent")

    def test_append_after_upgrade_is_strict(self):
        migration.upgrade(self.db)
        self.archive.append("extra", EXTRA_BUFFER)  # hidden junk tolerated, defaults apply
        self.assertEqual(self.archive.bounds("extra"), (0, 3))
        self.assertEqual(self.archive.read("extra"),
                         [{"station": "甲", "samples": [0, -1]}, None,
                          {"station": "乙", "samples": None}])
        self.assertEqual(sdk2.get(self.db, "extra"), EXTRA_CANONICAL)
        self.archive.append("part", EXTRA_BUFFER, 2, 1)  # sliced append keeps its coords
        self.assertEqual(self.archive.bounds("part"), (2, 1))
        self.assertEqual(self.archive.read("part"), [{"station": "乙", "samples": None}])
        with self.assertRaises(ValueError):
            self.archive.at("part", 1)
        count = self.db.execute("SELECT count(*) FROM batches2").fetchone()[0]
        mutations = []
        altered = copy.deepcopy(EXTRA_BUFFER); altered["station"][0] = 9
        mutations.append(altered)                        # reachable non-string station
        altered = copy.deepcopy(EXTRA_BUFFER); altered["offsets"][0] = -1
        mutations.append(altered)                        # reachable negative offset
        altered = copy.deepcopy(EXTRA_BUFFER); altered["values"][0] = "x"
        mutations.append(altered)                        # reachable non-int element
        altered = copy.deepcopy(EXTRA_BUFFER); altered["valid"][0] = 1
        mutations.append(altered)                        # non-boolean validity flag
        altered = copy.deepcopy(EXTRA_BUFFER); altered["offsets"] = [0, 2, 2]
        mutations.append(altered)                        # broken offset shape
        for i, changed in enumerate(mutations):
            with self.assertRaises(ValueError):
                self.archive.append("bad%d" % i, changed, 0, 3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM batches2").fetchone()[0], count)

    def test_mixed_native_migrated_and_appended_batches(self):
        migration.upgrade(self.db)
        sdk2.put(self.db, "native", NATIVE)  # native SDK2 write, caller commits
        self.db.commit()
        # Native batch is visible through the old interface, coordinates from 0.
        self.assertEqual(self.archive.bounds("native"), (0, 4))
        self.assertEqual(self.archive.read("native"),
                         [{"station": "甲", "samples": [0, -2]}, None,
                          {"station": "乙", "samples": None},
                          {"station": "丙", "samples": []}])
        self.archive.append("extra", EXTRA_BUFFER, 0, 3)
        # batch_id uniqueness is shared between native and archive appends.
        with self.assertRaises(ValueError):
            self.archive.append("native", EXTRA_BUFFER, 0, 1)
        with self.assertRaises(ValueError):
            self.archive.append("b", EXTRA_BUFFER, 0, 1)
        # Contract's mixed cohort: native rows are not re-shifted by old slices.
        self.cohort.create("mixed", [("b", 3), ("native", 1), ("b", 3), ("native", 0)])
        self.assertEqual(self.cohort.read("mixed"),
                         [EXPECTED_B[1], None, EXPECTED_B[1],
                          {"station": "甲", "samples": [0, -2]}])
        self.cohort.create("mixed2", [("extra", 0), ("native", 3), ("b", 2), ("extra", 2)])
        self.assertEqual(self.cohort.read("mixed2"),
                         [{"station": "甲", "samples": [0, -1]},
                          {"station": "丙", "samples": []},
                          {"station": "北站", "samples": [0, -2]},
                          {"station": "乙", "samples": None}])
        self.assertEqual(self.cohort.summary("mixed2"),
                         {"members": 4, "null_structs": 0, "null_lists": 1,
                          "empty_lists": 1, "value_count": 4, "value_sum": -3})

    def test_native_written_before_upgrade_survives(self):
        sdk2.install(self.db)
        sdk2.put(self.db, "pre", NATIVE)
        self.db.commit()
        self.assertEqual(migration.upgrade(self.db), 2)
        self.assertEqual(self.archive.bounds("pre"), (0, 4))
        self.assertEqual(self.archive.at("pre", 0), {"station": "甲", "samples": [0, -2]})


class FileUpgradeRegression(unittest.TestCase):
    def test_kill_before_commit_reopens_legacy_and_retries(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "store.db")
        child = (
            "import os, sqlite3, sys\n"
            "sys.path.insert(0, %r)\n"
            "import samples, migration\n"
            "db = sqlite3.connect(%r)\n"
            "samples.make(db)\n"
            "migration.upgrade(db, before_commit=lambda: os._exit(1))\n"
        ) % (WORKSPACE, path)
        proc = subprocess.run([sys.executable, "-c", child],
                              capture_output=True, text=True, cwd=WORKSPACE)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        # Only the complete legacy state survived the killed upgrade.
        self.assertEqual(migration.storage_version(db), "1")
        self.assertEqual(target_tables(db), set())
        self.assertEqual(db.execute("SELECT count(*) FROM batches1").fetchone()[0], 5)
        self.assertEqual(db.execute("SELECT count(*) FROM members1").fetchone()[0], len(MEMBERS))
        # Retry on the reopened file upgrades for real.
        self.assertEqual(migration.upgrade(db), 2)
        archive = Archive(db)
        cohort = Cohort(db, archive)
        self.assertEqual(archive.read("b"), EXPECTED_B)
        self.assertEqual(cohort.read("review"), EXPECTED_ROWS)
        self.assertEqual(cohort.summary("review"), EXPECTED_SUMMARY)
        self.assertEqual(migration.upgrade(db), 2)  # idempotent on files too

if __name__ == "__main__":
    unittest.main()
