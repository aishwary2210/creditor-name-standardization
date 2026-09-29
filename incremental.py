"""Add creditor names that appeared in the source since the last full run.

New COMPANY values are matched against the existing lookup and appended.
Existing mappings are left alone; row counts on existing canonicals are
refreshed the next time main.py runs.
"""

import db
from matching import COLUMNS, resolve


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

        mapped = dict(db.query(conn, f"SELECT NORMALIZED_NAME, CANONICAL_NAME FROM {db.table(db.ALIAS_MAP)}"))
        ids = {name: cid for cid, name in db.query(
            conn, f"SELECT CANONICAL_ID, CANONICAL_NAME FROM {db.table(db.CANONICALS)}")}
        lookup = {**{name: name for name in ids}, **mapped}

        result = resolve(raw_counts, lookup, db.load_overrides(conn))

        new_canonicals = [c for c in result.canonicals if c["canonical_name"] not in ids]
        next_id = max(ids.values(), default=0) + 1
        for offset, row in enumerate(new_canonicals):
            row["canonical_id"] = next_id + offset
            ids[row["canonical_name"]] = row["canonical_id"]

        new_aliases = [a for a in result.aliases if a["normalized_name"] not in mapped]
        for row in new_aliases:
            row["canonical_id"] = ids[row["canonical_name"]]

        db.write_table(conn, db.NORMALIZED, result.normalized, COLUMNS["normalized"], replace=False)
        db.write_table(conn, db.CANONICALS, new_canonicals, COLUMNS["canonicals"], replace=False)
        db.write_table(conn, db.ALIAS_MAP, new_aliases, COLUMNS["aliases"], replace=False)
        db.write_table(conn, db.REVIEW_QUEUE, result.review, COLUMNS["review"], replace=False)
    finally:
        conn.close()

    print(f"Added {len(new_aliases):,} names to the alias map, "
          f"{len(new_canonicals):,} new canonicals, {len(result.review):,} for review")


if __name__ == "__main__":
    main()
