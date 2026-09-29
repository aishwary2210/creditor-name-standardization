"""Run the matching steps on CSV files instead of Snowflake.

    python run_local.py
    python run_local.py --input my_names.csv --aliases my_aliases.csv --output out
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from matching import COLUMNS, load_aliases, resolve, suggest_merges

HERE = Path(__file__).parent


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, columns):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", default=HERE / "data/sample_creditors.csv",
                        help="CSV with raw_name,row_count")
    parser.add_argument("--aliases", default=HERE / "data/sample_aliases.csv",
                        help="CSV with alias,canonical_name")
    parser.add_argument("--output", default=HERE / "output", type=Path)
    args = parser.parse_args()

    raw_counts = defaultdict(int)
    for row in read_csv(args.input):
        raw_counts[row["raw_name"]] += int(row["row_count"])
    lookup = load_aliases((row["alias"], row["canonical_name"]) for row in read_csv(args.aliases))

    result = resolve(raw_counts, lookup)
    suggestions = suggest_merges(result.canonicals, result.review)

    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "creditors_normalized.csv", result.normalized, COLUMNS["normalized"])
    write_csv(args.output / "creditor_alias_map.csv", result.aliases, COLUMNS["aliases"])
    write_csv(args.output / "canonical_creditors.csv", result.canonicals, COLUMNS["canonicals"])
    write_csv(args.output / "creditor_review_queue.csv", result.review, COLUMNS["review"])
    write_csv(args.output / "merge_suggestions.csv", suggestions, COLUMNS["suggestions"])

    print(result.summary())
    print(f"{len(suggestions):,} merge suggestions")
    print(f"Wrote CSV files to {args.output}/")


if __name__ == "__main__":
    main()
