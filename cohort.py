"""Saved ordered analysis selections, independent of archive layout."""

class Cohort:
    def __init__(self, db, archive):
        self.db = db
        self.archive = archive

    def create(self, name, members):
        if type(name) is not str or not name:
            raise ValueError("name")
        members = list(members)
        for batch_id, physical_row in members:
            self.archive.at(batch_id, physical_row)
        with self.db:
            if self.db.execute("SELECT 1 FROM cohorts1 WHERE name=?", (name,)).fetchone():
                raise ValueError("duplicate cohort")
            self.db.execute("INSERT INTO cohorts1 VALUES (?)", (name,))
            self.db.executemany("INSERT INTO members1 VALUES (?,?,?,?)",
                               [(name, pos, bid, row) for pos, (bid, row) in enumerate(members)])

    def read(self, name):
        if not self.db.execute("SELECT 1 FROM cohorts1 WHERE name=?", (name,)).fetchone():
            raise KeyError(name)
        members = self.db.execute(
            "SELECT batch_id,physical_row FROM members1 WHERE name=? ORDER BY pos", (name,))
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
