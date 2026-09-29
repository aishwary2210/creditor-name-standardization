"""Rebuild the creditor lookup tables in Snowflake.

Reads distinct COMPANY values from the source table, runs the matching steps
and replaces CREDITORS_NORMALIZED, CANONICAL_CREDITORS, CREDITOR_ALIAS_MAP and
CREDITOR_REVIEW_QUEUE.
"""

import csv
import time

import db
from matching import COLUMNS, load_aliases, resolve


def main():
    start = time.time()
    conn = db.connect()
    try:
        raw_counts = dict(db.query(conn, f"""
            SELECT COMPANY, COUNT(*)
            FROM {db.SOURCE_TABLE}
            WHERE COMPANY IS NOT NULL AND COMPANY != '' AND _FIVETRAN_DELETED = FALSE
            GROUP BY COMPANY
        """))
        print(f"Read {len(raw_counts):,} distinct COMPANY values")

        with open(db.ALIASES_CSV, newline="", encoding="utf-8") as f:
            lookup = load_aliases((row["alias"], row["canonical_name"]) for row in csv.DictReader(f))
        overrides = db.load_overrides(conn)
        print(f"Loaded {len(lookup):,} curated aliases and {len(overrides):,} approved overrides")

        result = resolve(raw_counts, lookup, overrides)

        db.write_table(conn, db.NORMALIZED, result.normalized, COLUMNS["normalized"], replace=True)
        db.write_table(conn, db.CANONICALS, result.canonicals, COLUMNS["canonicals"], replace=True)
        db.write_table(conn, db.ALIAS_MAP, result.aliases, COLUMNS["aliases"], replace=True)
        db.write_table(conn, db.REVIEW_QUEUE, result.review, COLUMNS["review"], replace=True)
    finally:
        conn.close()

    print(result.summary())
    print(f"Finished in {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
