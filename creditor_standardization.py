"""Small, local demonstration of a creditor name resolution workflow."""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path


LEGAL_SUFFIXES = {"INC", "LLC", "LTD", "CORP"}
TOKEN_EXPANSIONS = {"BK": "BANK", "CR": "CREDIT"}
STOP_WORDS = {"AND", "OF", "THE"}
FUZZY_ACCEPT = 0.94
FUZZY_REVIEW = 0.84
CLUSTER_ACCEPT = 0.90


def normalize(raw_name: str) -> str:
    name = unicodedata.normalize("NFKD", raw_name).encode("ascii", "ignore").decode()
    name = re.sub(r"^\s*\d{2}\s+", "", name.upper())
    name = re.sub(r"\bDBA\b", " DBA ", name)
    if " DBA " in name:
        name = name.rsplit(" DBA ", 1)[1]
    tokens = re.findall(r"[A-Z0-9]+", name)
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(TOKEN_EXPANSIONS.get(token, token) for token in tokens)


def score(left: str, right: str) -> float:
    left_tokens = sorted(left.split())
    right_tokens = sorted(right.split())
    return SequenceMatcher(None, " ".join(left_tokens), " ".join(right_tokens)).ratio()


def soundex(word: str) -> str:
    """A small phonetic key for candidate generation, not a match decision."""
    if not word:
        return ""
    groups = {letter: digit for digit, letters in enumerate(
        ("AEIOUYHW", "BFPV", "CGJKQSXZ", "DT", "L", "MN", "R")
    ) for letter in letters}
    output = [word[0]]
    previous = groups.get(word[0], 0)
    for letter in word[1:]:
        digit = groups.get(letter, 0)
        if digit and digit != previous:
            output.append(str(digit))
        previous = digit
    return ("".join(output) + "000")[:4]


def block_keys(name: str) -> set[str]:
    tokens = [token for token in name.split() if token not in STOP_WORDS]
    compact = "".join(tokens)
    keys = set()
    if tokens:
        keys.add("token:" + tokens[0][:4])
        keys.add("sound:" + soundex(tokens[0]))
    if len(compact) >= 6:
        keys.add("edge:" + compact[:3] + ":" + compact[-3:])
    return keys


def can_cluster(left: str, right: str) -> bool:
    left_tokens = set(left.split()) - STOP_WORDS
    right_tokens = set(right.split()) - STOP_WORDS
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
    length_ratio = max(len(left), len(right)) / min(len(left), len(right))
    return overlap >= 0.5 and length_ratio <= 1.8 and score(left, right) >= CLUSTER_ACCEPT


def read_names(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_name = row["raw_name"].strip()
            count = int(row["row_count"])
            if not raw_name or not normalize(raw_name) or count < 1:
                raise ValueError("Every input row needs a name and a positive row_count")
            rows.append({"raw_name": raw_name, "row_count": count})
    if not rows:
        raise ValueError("The input CSV is empty")
    return rows


def read_aliases(path: Path) -> dict[str, str]:
    aliases = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            alias = normalize(row["alias"])
            canonical = normalize(row["canonical_name"])
            if not alias or not canonical:
                raise ValueError("Alias and canonical names must be nonblank")
            if alias in aliases and aliases[alias] != canonical:
                raise ValueError(f"Conflicting canonical names for {alias}")
            aliases[alias] = canonical
    return aliases


def resolve(names: list[dict], aliases: dict[str, str]) -> tuple[list[dict], list[dict], list[dict]]:
    raw_counts = defaultdict(int)
    for row in names:
        raw_counts[row["raw_name"]] += row["row_count"]
    normalized_counts = defaultdict(int)
    for raw_name, count in raw_counts.items():
        normalized_counts[normalize(raw_name)] += count

    canonicals = sorted(set(aliases.values()))
    decisions = {}
    review = []
    unmatched = []
    for name in sorted(normalized_counts):
        if name in aliases:
            decisions[name] = (aliases[name], "VERIFIED", "", "")
            continue
        match, similarity = max(((candidate, score(name, candidate)) for candidate in canonicals),
                                key=lambda pair: (pair[1], pair[0]), default=("", 0.0))
        if similarity >= FUZZY_ACCEPT:
            decisions[name] = (match, "FUZZY", "", f"{similarity:.3f}")
        elif similarity >= FUZZY_REVIEW:
            decisions[name] = (name, "REVIEW", match, f"{similarity:.3f}")
            review.append({"normalized_name": name, "suggested_match": match,
                           "score": f"{similarity:.3f}", "source_rows": normalized_counts[name]})
        else:
            unmatched.append(name)

    blocks = defaultdict(set)
    for name in unmatched:
        for key in block_keys(name):
            blocks[key].add(name)
    neighbors = defaultdict(set)
    for group in blocks.values():
        for name in group:
            neighbors[name].update(group - {name})

    available = set(unmatched)
    for representative in sorted(unmatched, key=lambda name: (-normalized_counts[name], name)):
        if representative not in available:
            continue
        members = sorted(candidate for candidate in neighbors[representative] & available
                         if can_cluster(representative, candidate))
        if members:
            decisions[representative] = (representative, "CLUSTERED", "", "")
            available.remove(representative)
            for member in members:
                decisions[member] = (representative, "CLUSTERED", "", f"{score(member, representative):.3f}")
                available.remove(member)
    for name in available:
        decisions[name] = (name, "STANDALONE", "", "")

    alias_rows = []
    canonical_counts = defaultdict(int)
    for raw_name, count in sorted(raw_counts.items()):
        normalized = normalize(raw_name)
        canonical, tier, suggested, similarity = decisions[normalized]
        alias_rows.append({"raw_name": raw_name, "normalized_name": normalized,
                           "canonical_name": canonical, "tier": tier, "source_rows": count,
                           "suggested_match": suggested, "score": similarity})
        canonical_counts[canonical] += count
    canonical_rows = [{"canonical_name": name, "source_rows": count} for name, count in sorted(canonical_counts.items())]
    return alias_rows, canonical_rows, review


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve names in a local creditor CSV")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--aliases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    aliases, canonicals, review = resolve(read_names(args.input), read_aliases(args.aliases))
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "alias_map.csv", aliases,
              ["raw_name", "normalized_name", "canonical_name", "tier", "source_rows", "suggested_match", "score"])
    write_csv(args.output / "canonical_creditors.csv", canonicals, ["canonical_name", "source_rows"])
    write_csv(args.output / "review_queue.csv", review,
              ["normalized_name", "suggested_match", "score", "source_rows"])
    print(f"{len(aliases)} source names; {len(canonicals)} canonical names; {len(review)} review candidates")


if __name__ == "__main__":
    main()
