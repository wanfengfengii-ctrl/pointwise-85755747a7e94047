"""Saved ordered analysis selections, independent of archive layout.

Members are always stored as the physical row coordinates given at create
time; on v2 storage they live in cohorts2/members2 and the archive resolves
them per batch (slice shift for archive batches, identity for native ones).
"""
import migration

class Cohort:
    def __init__(self, db, archive):
        self.db = db
        self.archive = archive

    def _tables(self):
        if migration.storage_version(self.db) == "2":
            return "cohorts2", "members2"
        return "cohorts1", "members1"

    def create(self, name, members):
        if type(name) is not str or not name:
            raise ValueError("name")
        members = list(members)
        for batch_id, physical_row in members:
            self.archive.at(batch_id, physical_row)
        cohorts, table = self._tables()
        with self.db:
            if self.db.execute("SELECT 1 FROM %s WHERE name=?" % cohorts, (name,)).fetchone():
                raise ValueError("duplicate cohort")
            self.db.execute("INSERT INTO %s VALUES (?)" % cohorts, (name,))
            self.db.executemany("INSERT INTO %s VALUES (?,?,?,?)" % table,
                               [(name, pos, bid, row) for pos, (bid, row) in enumerate(members)])

    def read(self, name):
        cohorts, table = self._tables()
        if not self.db.execute("SELECT 1 FROM %s WHERE name=?" % cohorts, (name,)).fetchone():
            raise KeyError(name)
        members = self.db.execute(
            "SELECT batch_id,physical_row FROM %s WHERE name=? ORDER BY pos" % table, (name,))
        return [self.archive.at(batch_id, row) for batch_id, row in members]

    def summary(self, name):
        rows = self.read(name)
        result = {"members": len(rows), "null_structs": 0, "null_lists": 0,
                  "empty_lists": 0, "value_count": 0, "value_sum": 0}
        for row in rows:
            if row is None:
                result["null_structs"] += 1
            elif row["samples"] is None:
                result["null_lists"] += 1
            else:
                values = row["samples"]
                result["empty_lists"] += not values
                result["value_count"] += len(values)
                result["value_sum"] += sum(values)
        return result
