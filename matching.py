"""Matching logic shared by the local runner and the Snowflake scripts.

Nothing in this module talks to a database. It takes raw names with row
counts and returns the rows for the lookup tables.
"""

from collections import defaultdict
from dataclasses import dataclass

import jellyfish
from rapidfuzz import fuzz, process

from normalize import normalize_company

# Scores are 0-100. These were set by reviewing samples of matches at each level.
AUTO_ACCEPT = 95         # accept a fuzzy match to a known creditor
REVIEW_MIN = 93          # between REVIEW_MIN and AUTO_ACCEPT, a person decides
CLUSTER_MIN = 90         # group two names that matched nothing known
HIGH_VOLUME_ROWS = 5000  # if both sides are this large, always ask a person
MIN_TOKEN_OVERLAP = 0.5
MAX_LENGTH_RATIO = 2.0
MAX_BLOCK_SIZE = 500     # keeps a common first word from creating a huge block
LOW_FREQUENCY_ROWS = 10

STOP_WORDS = {"THE", "A", "AN", "OF", "AND", "FOR", "IN", "AT", "BY", "TO", "OR"}

COLUMNS = {
    "normalized": ["raw_name", "normalized_name", "source_rows"],
    "aliases": ["normalized_name", "canonical_id", "canonical_name", "tier", "score", "source_rows"],
    "canonicals": ["canonical_id", "canonical_name", "tier", "source_rows"],
    "review": ["normalized_name", "suggested_canonical", "score", "source_rows", "high_volume"],
    "suggestions": ["current_name", "suggested_canonical", "reason", "source_rows"],
}


@dataclass
class Result:
    normalized: list
    aliases: list
    canonicals: list
    review: list
    candidate_pairs: int

    def summary(self):
        tiers = defaultdict(int)
        for row in self.aliases:
            tiers[row["tier"]] += 1
        lines = [
            f"{len(self.normalized):,} raw names -> {len(self.aliases):,} normalized names "
            f"-> {len(self.canonicals):,} canonical creditors",
            f"{self.candidate_pairs:,} candidate pairs compared during clustering",
            f"{len(self.review):,} names sent to review",
        ]
        lines += [f"  {tier:<14}{count:,}" for tier, count in sorted(tiers.items())]
        return "\n".join(lines)


def load_aliases(pairs):
    """Build a {normalized name: canonical name} lookup from (alias, canonical) pairs."""
    lookup = {}
    for alias, canonical in pairs:
        canonical = canonical.strip().upper()
        for key in (normalize_company(alias), normalize_company(canonical)):
            if not key:
                raise ValueError(f"Alias {alias!r} is blank after cleaning")
            if lookup.get(key, canonical) != canonical:
                raise ValueError(f"{key!r} maps to both {lookup[key]!r} and {canonical!r}")
            lookup[key] = canonical
    return lookup


def tokens(name):
    return [t for t in name.split() if t not in STOP_WORDS and len(t) > 1 and not t.isdigit()]


def block_keys(name):
    """Keys used to pick which names get compared.

    Two names are only scored if they share at least one key: the first two
    words, the sound of the first word, or the first and last four letters.
    """
    words = tokens(name)
    keys = set()
    if len(words) >= 2:
        keys.add(f"word:{words[0]}_{words[1][:4]}")
    elif words and len(words[0]) >= 4:
        keys.add(f"word:{words[0]}")
    if words and len(words[0]) >= 3:
        keys.add(f"sound:{jellyfish.metaphone(words[0])}")
    compact = name.replace(" ", "")
    if len(compact) >= 8:
        keys.add(f"ends:{compact[:4]}_{compact[-4:]}")
    elif len(compact) >= 4:
        keys.add(f"ends:{compact[:4]}")
    return keys


def similarity(a, b):
    # Jaro-Winkler handles typos in short names; token sort handles word order in long ones.
    if len(a) <= 20 and len(b) <= 20:
        return jellyfish.jaro_winkler_similarity(a, b) * 100
    return fuzz.token_sort_ratio(a, b)


def can_merge(a, b):
    """Cheap checks first, so "GREEN DOT BANK" never gets scored against "GREEN LAWN"."""
    if max(len(a), len(b)) / min(len(a), len(b)) > MAX_LENGTH_RATIO:
        return False
    words_a, words_b = set(tokens(a)), set(tokens(b))
    if not words_a or not words_b:
        return False
    if len(words_a & words_b) / min(len(words_a), len(words_b)) < MIN_TOKEN_OVERLAP:
        return False
    return similarity(a, b) >= CLUSTER_MIN


def cluster(names, rows):
    """Group names that matched nothing known.

    Returns ({name: (representative, score)}, number of candidate pairs).
    The most common name in a group is its representative, and every member
    must match the representative directly, so A~B and B~C does not pull C in.
    """
    blocks = defaultdict(list)
    for name in names:
        for key in block_keys(name):
            blocks[key].append(name)

    neighbors = defaultdict(set)
    for members in blocks.values():
        if len(members) > MAX_BLOCK_SIZE:
            members = sorted(members, key=lambda n: (-rows[n], n))[:MAX_BLOCK_SIZE]
        for name in members:
            neighbors[name].update(members)
    for name in neighbors:
        neighbors[name].discard(name)
    candidate_pairs = sum(len(v) for v in neighbors.values()) // 2

    assigned = {}
    for rep in sorted(names, key=lambda n: (-rows[n], n)):
        if rep in assigned:
            continue
        for other in sorted(neighbors[rep]):
            if other not in assigned and can_merge(rep, other):
                assigned[other] = (rep, round(similarity(rep, other), 1))
                assigned[rep] = (rep, None)
    return assigned, candidate_pairs


def resolve(raw_counts, lookup, overrides=None):
    """Map every raw name to exactly one canonical creditor.

    raw_counts: {raw name: source rows}
    lookup:     {normalized name: canonical name} from the curated alias list
    overrides:  {normalized name: canonical name} approved in the review app
    """
    overrides = overrides or {}
    known = {**lookup, **overrides}
    choices = sorted(set(known.values()))

    normalized = []
    rows = defaultdict(int)
    for raw, count in sorted(raw_counts.items()):
        name = normalize_company(raw)
        if name:
            normalized.append({"raw_name": raw, "normalized_name": name, "source_rows": count})
            rows[name] += count

    known_rows = defaultdict(int)
    for name, count in rows.items():
        if name in known:
            known_rows[known[name]] += count

    decisions = {}  # normalized name -> (canonical, tier, score)
    review = []
    unmatched = []
    for name in sorted(rows, key=lambda n: (-rows[n], n)):
        if name in known:
            decisions[name] = (known[name], "MANUAL" if name in overrides else "VERIFIED", None)
            continue
        match = process.extractOne(name, choices, scorer=fuzz.WRatio, score_cutoff=REVIEW_MIN)
        if match is None:
            unmatched.append(name)
            continue
        canonical, score = match[0], round(match[1], 1)
        high_volume = rows[name] >= HIGH_VOLUME_ROWS and known_rows[canonical] >= HIGH_VOLUME_ROWS
        if score >= AUTO_ACCEPT and not high_volume:
            decisions[name] = (canonical, "FUZZY", score)
        else:
            # The name stays its own creditor until someone approves the merge.
            decisions[name] = (name, "REVIEW", score)
            review.append({"normalized_name": name, "suggested_canonical": canonical,
                           "score": score, "source_rows": rows[name], "high_volume": high_volume})

    clusters, candidate_pairs = cluster(unmatched, rows)
    for name in unmatched:
        if name in clusters:
            rep, score = clusters[name]
            decisions[name] = (rep, "CLUSTERED", score)
        elif rows[name] >= LOW_FREQUENCY_ROWS:
            decisions[name] = (name, "STANDALONE", None)
        else:
            decisions[name] = (name, "LOW_FREQUENCY", None)

    canonical_rows = defaultdict(int)
    canonical_tier = {name: "VERIFIED" for name in choices}
    for name, (canonical, tier, _) in decisions.items():
        canonical_rows[canonical] += rows[name]
        canonical_tier.setdefault(canonical, tier)

    ordered = sorted(canonical_tier, key=lambda n: (-canonical_rows[n], n))
    ids = {name: i for i, name in enumerate(ordered, start=1)}
    canonicals = [{"canonical_id": ids[name], "canonical_name": name,
                   "tier": canonical_tier[name], "source_rows": canonical_rows[name]}
                  for name in ordered]
    aliases = [{"normalized_name": name, "canonical_id": ids[canonical],
                "canonical_name": canonical, "tier": tier, "score": score,
                "source_rows": rows[name]}
               for name, (canonical, tier, score) in sorted(decisions.items())]
    return Result(normalized, aliases, canonicals, review, candidate_pairs)


def suggest_merges(canonicals, review, decided=()):
    """Suggest merges for names the automatic steps left on their own.

    - prefix: starts with a verified multi-word name ("ASTER BANK AUTO" -> "ASTER BANK")
    - split: one side of a "/" or "&" is verified ("ASTER BANK/RETAIL" -> "ASTER BANK")
    - high_volume_prefix: an unverified name with 5,000+ rows is the prefix of a
      name with less than half its rows
    - near_match: fuzzy matches that were held for review

    decided holds (name, suggested canonical) pairs a reviewer already approved
    or rejected, so they are not asked again.
    """
    decided = set(decided)
    verified = {c["canonical_name"] for c in canonicals if c["tier"] == "VERIFIED"}
    unverified = {c["canonical_name"]: c["source_rows"] for c in canonicals
                  if c["canonical_name"] not in verified}
    large = {name: count for name, count in unverified.items()
             if count >= HIGH_VOLUME_ROWS and " " in name}

    def longest_prefix(name, candidates):
        words = name.split()
        for size in range(len(words) - 1, 1, -1):
            prefix = " ".join(words[:size])
            if prefix in candidates:
                return prefix
        return None

    suggestions = {}

    def add(name, target, reason):
        if not target or name in suggestions or (name, target) in decided:
            return False
        suggestions[name] = {"current_name": name, "suggested_canonical": target,
                             "reason": reason, "source_rows": unverified[name]}
        return True

    for name, count in unverified.items():
        parts = [p.strip() for piece in name.split("/") for p in piece.split("&")]
        candidates = [(longest_prefix(name, verified), "prefix"),
                      (next((p for p in parts if p in verified), None), "split")]
        big = longest_prefix(name, large)
        if big and count < large[big] / 2:
            candidates.append((big, "high_volume_prefix"))
        for target, reason in candidates:
            if add(name, target, reason):
                break

    for row in review:
        if row["normalized_name"] in unverified:
            add(row["normalized_name"], row["suggested_canonical"], "near_match")

    return sorted(suggestions.values(), key=lambda s: (-s["source_rows"], s["current_name"]))
