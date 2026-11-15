"""Observation archive: SDK1 on legacy v1 storage, SDK2 after upgrade.

The storage version is read on every call, so existing objects switch to the
new storage the moment migration.upgrade commits; callers never rebuild them.
On v2 storage all reads and appends go through SDK2 and the target tables
only; SDK1 and the v1 audit tables are no longer touched.
"""
import hashlib
import json
from vendor import sdk1, sdk2
import migration

class Archive:
    def __init__(self, db):
        self.db = db

    def _v2(self):
        return migration.storage_version(self.db) == "2"

    def append(self, batch_id, buffer, offset=0, length=None):
        if type(batch_id) is not str or not batch_id:
            raise ValueError("batch_id")
        if length is None:
            length = len(buffer["valid"]) - offset
        if self._v2():
            # Same legacy buffer/slice signature, same strict reachable-value
            # checks as the migration; stored as a canonical SDK2 batch.
            batch = migration.canonical_slice(buffer, offset, length)
            with self.db:
                if self.db.execute("SELECT 1 FROM batches2 WHERE batch_id=?",
                                   (batch_id,)).fetchone():
                    raise ValueError("duplicate batch")
                sdk2.put(self.db, batch_id, batch)
                self.db.execute("INSERT INTO slices2 VALUES (?,?,?)",
                                (batch_id, offset, length))
            return
        sdk1.check(buffer, offset, length)
        payload = json.dumps(buffer, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        buffer_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self.db:
            if self.db.execute("SELECT 1 FROM batches1 WHERE batch_id=?", (batch_id,)).fetchone():
                raise ValueError("duplicate batch")
            self.db.execute("INSERT OR IGNORE INTO buffers1 VALUES (?,?)", (buffer_id, payload))
            self.db.execute("INSERT INTO batches1 VALUES (?,?,?,?)",
                            (batch_id, buffer_id, offset, length))

    def bounds(self, batch_id):
        if self._v2():
            item = self.db.execute("SELECT start_row,row_count FROM slices2 WHERE batch_id=?",
                                   (batch_id,)).fetchone()
            if item is not None:
                return tuple(item)
            batch = sdk2.get(self.db, batch_id)  # KeyError when absent
            return (0, len(batch["valid"]))  # native SDK2 batch: rows from 0
        item = self.db.execute("SELECT start_row,row_count FROM batches1 WHERE batch_id=?",
                               (batch_id,)).fetchone()
        if item is None:
            raise KeyError(batch_id)
        return tuple(item)

    def at(self, batch_id, physical_row):
        start, count = self.bounds(batch_id)
        if type(physical_row) is not int or not start <= physical_row < start + count:
            raise ValueError("member outside slice")
        if self._v2():
            return sdk2.row(sdk2.get(self.db, batch_id), physical_row - start)
        payload = self.db.execute(
            "SELECT payload FROM buffers1 JOIN batches1 USING(buffer_id) WHERE batch_id=?",
            (batch_id,)).fetchone()[0]
        return sdk1.row(json.loads(payload), physical_row)

    def read(self, batch_id):
        start, count = self.bounds(batch_id)
        return [self.at(batch_id, i) for i in range(start, start + count)]
