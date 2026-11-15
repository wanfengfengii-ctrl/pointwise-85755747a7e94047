import json
from samples import memory

def main():
    db, archive, cohort = memory()
    print(json.dumps({"storage_version": db.execute(
        "SELECT value FROM meta WHERE key='storage_version'").fetchone()[0],
        "slice_b": archive.read("b"), "review": cohort.read("review"),
        "summary": cohort.summary("review")}, ensure_ascii=False, indent=2))
    db.close()

if __name__ == "__main__":
    main()
