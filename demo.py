"""Demo: legacy v1 reads, the real upgrade, then mixed old/new analysis."""
import json
import migration
from samples import BUFFER, memory
from vendor import sdk2

NATIVE = {"valid": [True, False, True, True], "station": ["甲", "", "乙", "丙"],
          "list_valid": [True, False, False, True], "offsets": [0, 2, 2, 2, 2],
          "values": [0, -2]}
EXTRA = {"valid": [True, False, True], "station": ["甲", 9, "乙"],
         "list_valid": [True, True, False], "offsets": [0, 2, 2, 4],
         "values": [0, -1, "junk", None]}

def snapshot(db, archive, cohort):
    return {"storage_version": db.execute(
        "SELECT value FROM meta WHERE key='storage_version'").fetchone()[0],
        "slice_b": archive.read("b"), "review": cohort.read("review"),
        "summary": cohort.summary("review")}

def main():
    db, archive, cohort = memory()
    out = {"before_upgrade": snapshot(db, archive, cohort)}
    migration.upgrade(db)
    out["after_upgrade"] = snapshot(db, archive, cohort)
    sdk2.put(db, "native", NATIVE)          # native SDK2 append, caller commits
    db.commit()
    archive.append("extra", EXTRA, 0, 3)    # old-interface slice append on v2
    cohort.create("mixed", [("b", 3), ("native", 1), ("extra", 0),
                            ("b", 3), ("native", 0), ("extra", 2)])
    out["mixed_analysis"] = {
        "native_bounds": archive.bounds("native"),
        "extra_bounds": archive.bounds("extra"),
        "mixed_rows": cohort.read("mixed"),
        "mixed_summary": cohort.summary("mixed")}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    db.close()

if __name__ == "__main__":
    main()
