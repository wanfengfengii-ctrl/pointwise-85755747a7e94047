"""Existing observation archive; SDK1 only."""
import hashlib
import json
from vendor import sdk1

class Archive:
    def __init__(self, db):
        self.db = db

    def append(self, batch_id, buffer, offset=0, length=None):
        if type(batch_id) is not str or not batch_id:
            raise ValueError("batch_id")
        if length is None:
            length = len(buffer["valid"]) - offset
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
        item = self.db.execute("SELECT start_row,row_count FROM batches1 WHERE batch_id=?",
                               (batch_id,)).fetchone()
        if item is None:
            raise KeyError(batch_id)
        return tuple(item)

    def at(self, batch_id, physical_row):
        start, count = self.bounds(batch_id)
        if type(physical_row) is not int or not start <= physical_row < start + count:
            raise ValueError("member outside slice")
        payload = self.db.execute(
            "SELECT payload FROM buffers1 JOIN batches1 USING(buffer_id) WHERE batch_id=?",
            (batch_id,)).fetchone()[0]
        return sdk1.row(json.loads(payload), physical_row)

    def read(self, batch_id):
        start, count = self.bounds(batch_id)
        return [self.at(batch_id, i) for i in range(start, start + count)]
