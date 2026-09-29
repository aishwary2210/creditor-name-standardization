"""Add creditor names that appeared in the source since the last full run.

New COMPANY values are matched against the existing lookup and appended.
Existing mappings are left alone; row counts on existing canonicals are
refreshed the next time main.py runs. If a run fails partway, run it again
(see matching.plan_incremental).
"""

import db
from matching import COLUMNS, plan_incremental

TABLES = {
    "canonicals": db.CANONICALS,
    "review": db.REVIEW_QUEUE,
    "aliases": db.ALIAS_MAP,
    "normalized": db.NORMALIZED,
}


def main():
    conn = db.connect()
    try:
        raw_counts = dict(db.query(conn, f"""
            SELECT s.COMPANY, COUNT(*)
            FROM {db.SOURCE_TABLE} s
            WHERE s.COMPANY IS NOT NULL AND s.COMPANY != '' AND s._FIVETRAN_DELETED = FALSE
              AND NOT EXISTS (
                  SELECT 1 FROM {db.table(db.NORMALIZED)} n WHERE n.RAW_NAME = s.COMPANY
              )
            GROUP BY s.COMPANY
        """))
        if not raw_counts:
            print("No new names.")
            return
        print(f"Found {len(raw_counts):,} new COMPANY values")

        canonicals = db.query(conn, f"SELECT CANONICAL_ID, CANONICAL_NAME, TIER FROM {db.table(db.CANONICALS)}")
        mapped = dict(db.query(conn, f"SELECT NORMALIZED_NAME, CANONICAL_NAME FROM {db.table(db.ALIAS_MAP)}"))
        queued = set()
        if db.table_exists(conn, db.REVIEW_QUEUE):
            queued = {name for (name,) in db.query(
                conn, f"SELECT NORMALIZED_NAME FROM {db.table(db.REVIEW_QUEUE)}")}

        plan = plan_incremental(raw_counts, canonicals, mapped, queued, db.load_overrides(conn))
        for key, rows in plan.items():  # plan is already in write order
            db.write_table(conn, TABLES[key], rows, COLUMNS[key], replace=False)
    finally:
        conn.close()

    print(f"Added {len(plan['aliases']):,} names to the alias map, "
          f"{len(plan['canonicals']):,} new canonicals, {len(plan['review']):,} for review")


if __name__ == "__main__":
    main()
