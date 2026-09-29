"""Write merge suggestions for the review app.

Run after main.py. See matching.suggest_merges for the rules.
"""

import db
from matching import COLUMNS, suggest_merges


def main():
    conn = db.connect()
    try:
        canonicals = [{"canonical_name": name, "tier": tier, "source_rows": rows}
                      for name, tier, rows in db.query(conn, f"""
                          SELECT CANONICAL_NAME, TIER, SOURCE_ROWS FROM {db.table(db.CANONICALS)}
                      """)]
        try:
            review = [{"normalized_name": name, "suggested_canonical": target}
                      for name, target in db.query(conn, f"""
                          SELECT NORMALIZED_NAME, SUGGESTED_CANONICAL FROM {db.table(db.REVIEW_QUEUE)}
                      """)]
        except db.ProgrammingError:
            review = []  # main.py drops the table when nothing needs review

        suggestions = suggest_merges(canonicals, review)
        for row in suggestions:
            row["status"] = "pending"
        db.write_table(conn, db.SUGGESTIONS, suggestions, COLUMNS["suggestions"] + ["status"],
                       replace=True)
    finally:
        conn.close()

    by_reason = {}
    for row in suggestions:
        by_reason[row["reason"]] = by_reason.get(row["reason"], 0) + 1
    print(f"{len(suggestions):,} suggestions covering "
          f"{sum(row['source_rows'] for row in suggestions):,} source rows")
    for reason, count in sorted(by_reason.items()):
        print(f"  {reason:<20}{count:,}")


if __name__ == "__main__":
    main()
