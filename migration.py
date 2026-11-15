"""v1 -> v2 storage upgrade: strict slice conversion and atomic cutover.

Target layout (the four v1 tables are kept untouched for audit):
  batches2(batch_id, payload)                 canonical SDK2 batches, rows 0..n
  slices2(batch_id, start_row, row_count)     original slice of each archive batch
  cohorts2(name), members2(name, pos, batch_id, physical_row)
                                              saved selections, still bound to the
                                              original physical row coordinates
"""
import json
from vendor import sdk2

DDL = (
    "CREATE TABLE IF NOT EXISTS slices2("
    "batch_id TEXT PRIMARY KEY, start_row INTEGER NOT NULL, row_count INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS cohorts2(name TEXT PRIMARY KEY)",
    "CREATE TABLE IF NOT EXISTS members2("
    "name TEXT NOT NULL REFERENCES cohorts2(name), pos INTEGER NOT NULL, "
    "batch_id TEXT NOT NULL REFERENCES batches2(batch_id), "
    "physical_row INTEGER NOT NULL, PRIMARY KEY(name,pos))",
)


def storage_version(db):
    row = db.execute("SELECT value FROM meta WHERE key='storage_version'").fetchone()
    return row[0] if row is not None else None


def canonical_slice(buffer, offset, length):
    """Strictly convert one legacy slice into a canonical SDK2 batch.

    Structure (array shapes, boolean validity flags, slice bounds) is always
    checked.  Logical values are checked only where reachable: a reachable
    station must be a string, reachable offsets must be non-boolean ints with
    0 <= a <= b <= len(values), and every covered element must be a
    non-boolean int.  Anything hidden outside the slice, below a null struct
    or null list, or outside covered spans is dropped, never inspected.
    Raises ValueError on any reachable bad value.
    """
    n = len(buffer["valid"])
    if any(len(buffer[k]) != n for k in ("station", "list_valid")):
        raise ValueError("shape")
    if len(buffer["offsets"]) != n + 1:
        raise ValueError("offset shape")
    if type(offset) is not int or type(length) is not int:
        raise ValueError("slice")
    if offset < 0 or length < 0 or offset + length > n:
        raise ValueError("slice")
    if any(type(v) is not bool for v in buffer["valid"] + buffer["list_valid"]):
        raise ValueError("validity")
    batch = {"valid": [], "station": [], "list_valid": [], "offsets": [0], "values": []}
    for i in range(offset, offset + length):
        if not buffer["valid"][i]:
            batch["valid"].append(False)
            batch["station"].append("")
            batch["list_valid"].append(False)
            batch["offsets"].append(batch["offsets"][-1])
            continue
        station = buffer["station"][i]
        if type(station) is not str:
            raise ValueError("reachable station")
        batch["valid"].append(True)
        batch["station"].append(station)
        if not buffer["list_valid"][i]:
            batch["list_valid"].append(False)
            batch["offsets"].append(batch["offsets"][-1])
            continue
        a, b = buffer["offsets"][i], buffer["offsets"][i + 1]
        if type(a) is not int or type(b) is not int or not 0 <= a <= b <= len(buffer["values"]):
            raise ValueError("reachable offsets")
        span = buffer["values"][a:b]
        if any(type(v) is not int for v in span):
            raise ValueError("reachable values")
        batch["list_valid"].append(True)
        batch["values"].extend(span)
        batch["offsets"].append(batch["offsets"][-1] + len(span))
    sdk2.validate(batch)
    return batch


def upgrade(db, before_commit=None):
    """Migrate every batch and saved selection to SDK2 storage, atomically.

    Returns 2 on success or when already upgraded.  All conversion happens
    before any write, so a ValueError leaves the database untouched.  Every
    target write (batches2, slices2, cohorts2, members2, meta) commits in one
    transaction; before_commit runs once after staging, before the commit.
    """
    version = storage_version(db)
    if version == "2":
        return 2
    if version != "1":
        raise ValueError("unknown storage version")
    # Pure conversion first: any ValueError here leaves the database untouched.
    batches = []
    for batch_id, start, count, payload in db.execute(
            "SELECT b.batch_id, b.start_row, b.row_count, f.payload "
            "FROM batches1 b JOIN buffers1 f ON f.buffer_id = b.buffer_id "
            "ORDER BY b.batch_id"):
        batches.append((batch_id, start, count,
                        canonical_slice(json.loads(payload), start, count)))
    cohorts = [r[0] for r in db.execute("SELECT name FROM cohorts1 ORDER BY name")]
    members = db.execute(
        "SELECT name, pos, batch_id, physical_row FROM members1 ORDER BY name, pos").fetchall()
    db.execute("BEGIN")
    try:
        sdk2.install(db)
        for statement in DDL:
            db.execute(statement)
        for batch_id, start, count, batch in batches:
            sdk2.put(db, batch_id, batch)
            db.execute("INSERT INTO slices2 VALUES (?,?,?)", (batch_id, start, count))
        db.executemany("INSERT INTO cohorts2 VALUES (?)", [(name,) for name in cohorts])
        db.executemany("INSERT INTO members2 VALUES (?,?,?,?)", members)
        db.execute("UPDATE meta SET value='2' WHERE key='storage_version'")
        if before_commit is not None:
            before_commit()
    except BaseException:
        db.rollback()
        raise
    db.commit()
    return 2
