"""Write merge suggestions for the review app.

Run after main.py. See matching.suggest_merges for the rules. Suggestions a
reviewer already approved or rejected are kept as they are and not asked again.
"""

import db
from matching import COLUMNS, suggest_merges

SUGGESTION_COLUMNS = COLUMNS["suggestions"] + ["status"]


def main():
    conn = db.connect()
    try:
        canonicals = [{"canonical_name": name, "tier": tier, "source_rows": rows}
                      for name, tier, rows in db.query(conn, f"""
                          SELECT CANONICAL_NAME, TIER, SOURCE_ROWS FROM {db.table(db.CANONICALS)}
                      """)]

        review = []  # main.py drops the review table when nothing needs review
        if db.table_exists(conn, db.REVIEW_QUEUE):
            review = [{"normalized_name": name, "suggested_canonical": target}
                      for name, target in db.query(conn, f"""
                          SELECT NORMALIZED_NAME, SUGGESTED_CANONICAL FROM {db.table(db.REVIEW_QUEUE)}
                      """)]

        decided = []
        if db.table_exists(conn, db.SUGGESTIONS):
            columns = ", ".join(c.upper() for c in SUGGESTION_COLUMNS)
            decided = [dict(zip(SUGGESTION_COLUMNS, row)) for row in db.query(conn, f"""
                SELECT {columns} FROM {db.table(db.SUGGESTIONS)} WHERE STATUS != 'pending'
            """)]

        suggestions = suggest_merges(
            canonicals, review,
            decided=[(row["current_name"], row["suggested_canonical"]) for row in decided])
        for row in suggestions:
            row["status"] = "pending"
        db.write_table(conn, db.SUGGESTIONS, decided + suggestions, SUGGESTION_COLUMNS,
                       replace=True)
    finally:
        conn.close()

    by_reason = {}
    for row in suggestions:
        by_reason[row["reason"]] = by_reason.get(row["reason"], 0) + 1
    print(f"{len(suggestions):,} new suggestions covering "
          f"{sum(row['source_rows'] for row in suggestions):,} source rows")
    for reason, count in sorted(by_reason.items()):
        print(f"  {reason:<20}{count:,}")


if __name__ == "__main__":
    main()
