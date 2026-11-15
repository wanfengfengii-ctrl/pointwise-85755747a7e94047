"""Frozen complete v1 schema, shared buffers and saved selections."""
import copy
import sqlite3
from archive import Archive
from cohort import Cohort

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta VALUES ('storage_version','1');
CREATE TABLE buffers1(buffer_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE batches1(
  batch_id TEXT PRIMARY KEY,
  buffer_id TEXT NOT NULL REFERENCES buffers1(buffer_id),
  start_row INTEGER NOT NULL,
  row_count INTEGER NOT NULL);
CREATE TABLE cohorts1(name TEXT PRIMARY KEY);
CREATE TABLE members1(
  name TEXT NOT NULL REFERENCES cohorts1(name),
  pos INTEGER NOT NULL,
  batch_id TEXT NOT NULL REFERENCES batches1(batch_id),
  physical_row INTEGER NOT NULL,
  PRIMARY KEY(name,pos));
"""

BUFFER = {
    "valid": [True, False, True, True, True, True, False, True],
    "station": [17, {"bad": "hidden"}, "北站", "南站", "空列", "缺列", None, "尾站"],
    "list_valid": [True, True, True, True, True, False, True, True],
    "offsets": [-40, -9, 2, 4, 5, 5, 7, 8, 9],
    "values": ["unused", False, 0, -2, 7, "hidden", None, 99, 100]
}
MEMBERS = [("b", 3), ("b", 2), ("b", 3), ("a", 1),
           ("tail", 4), ("tail", 5), ("tail", 6)]
EXPECTED_B = [{"station": "北站", "samples": [0, -2]},
              {"station": "南站", "samples": [7]}]
EXPECTED_ROWS = [EXPECTED_B[1], EXPECTED_B[0], EXPECTED_B[1],
                 None, {"station": "空列", "samples": []},
                 {"station": "缺列", "samples": None}, None]
EXPECTED_SUMMARY = {"members": 7, "null_structs": 2, "null_lists": 1,
                    "empty_lists": 1, "value_count": 4, "value_sum": 12}

def make(db, bad=False):
    db.executescript(SCHEMA)
    db.execute("PRAGMA foreign_keys=ON")
    archive = Archive(db)
    for bid, start, count in [("a", 1, 4), ("b", 2, 2), ("tail", 4, 3), ("zero", 8, 0), ("unselected", 7, 1)]:
        archive.append(bid, copy.deepcopy(BUFFER), start, count)
    cohort = Cohort(db, archive)
    cohort.create("review", MEMBERS)
    cohort.create("empty", [])
    if bad:
        import json
        payload = copy.deepcopy(BUFFER)
        payload["station"][2] = 31
        db.execute("UPDATE buffers1 SET payload=?", (json.dumps(payload, ensure_ascii=False),))
        db.commit()
    return archive, cohort

def memory(bad=False):
    db = sqlite3.connect(":memory:")
    return db, *make(db, bad)
