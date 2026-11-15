"""Bundled CanonicalBatch SDK 2. No legacy decoder or migration."""
import json

KEYS = {"valid", "station", "list_valid", "offsets", "values"}

def validate(batch):
    if type(batch) is not dict or set(batch) != KEYS:
        raise ValueError("canonical keys")
    if any(type(batch[k]) is not list for k in KEYS):
        raise ValueError("array")
    n = len(batch["valid"])
    if len(batch["station"]) != n or len(batch["list_valid"]) != n:
        raise ValueError("shape")
    offsets = batch["offsets"]
    if len(offsets) != n + 1:
        raise ValueError("offset shape")
    if any(type(x) is not bool for x in batch["valid"] + batch["list_valid"]):
        raise ValueError("validity")
    if any(type(x) is not str for x in batch["station"]):
        raise ValueError("station type")
    if any(type(x) is not int for x in batch["values"]):
        raise ValueError("element type")
    if any(type(x) is not int for x in offsets):
        raise ValueError("offset type")
    if offsets[0] != 0 or offsets[-1] != len(batch["values"]):
        raise ValueError("offset endpoints")
    if any(a > b for a, b in zip(offsets, offsets[1:])):
        raise ValueError("offset order")
    for i in range(n):
        if not batch["valid"][i]:
            if batch["station"][i] != "" or batch["list_valid"][i]:
                raise ValueError("null struct children")
        if not batch["valid"][i] or not batch["list_valid"][i]:
            if offsets[i] != offsets[i + 1]:
                raise ValueError("null span")

def install(db):
    db.execute("CREATE TABLE IF NOT EXISTS batches2(batch_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

def put(db, batch_id, batch):
    validate(batch)
    db.execute("INSERT INTO batches2 VALUES (?,?)",
               (batch_id, json.dumps(batch, ensure_ascii=False, separators=(",", ":"))))

def get(db, batch_id):
    record = db.execute("SELECT payload FROM batches2 WHERE batch_id=?", (batch_id,)).fetchone()
    if record is None:
        raise KeyError(batch_id)
    batch = json.loads(record[0])
    validate(batch)
    return batch

def row(batch, row_index):
    if type(row_index) is not int or not 0 <= row_index < len(batch["valid"]):
        raise ValueError("row")
    if not batch["valid"][row_index]:
        return None
    a, b = batch["offsets"][row_index:row_index + 2]
    return {"station": batch["station"][row_index],
            "samples": batch["values"][a:b] if batch["list_valid"][row_index] else None}
