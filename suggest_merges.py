"""
Auto-Suggest Merge Candidates for Creditor Standardization
===========================================================
Scans CANONICAL_CREDITORS and finds names that should be merged but weren't
caught by the fuzzy/clustering pipeline (abbreviations, slashes, jammed names).

Heuristics:
  A. Known canonical as prefix (CAPITAL ONE AUTO FINAN → CAPITAL ONE)
  B. Substring containment (small name inside larger canonical)
  C. Slash/ampersand splitting (CAPITAL ONE/L&T → CAPITAL ONE)
  D. Concatenation detection (CAPONEAUTO → CAP ONE AUTO → CAPITAL ONE)

Output: writes to CREDITOR_MERGE_SUGGESTIONS in Snowflake.

Usage:
  python suggest_merges.py
"""

import time
from datetime import datetime, timezone

import polars as pl
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
import pandas as pd

from config import (
    SNOWFLAKE_CONFIG, OUTPUT_TABLES, TARGET_SCHEMA,
    TARGET_DATABASE, TARGET_SCHEMA_NAME,
)
from seed_aliases import ALIAS_MAP, CANONICAL_NAMES
from normalize import normalize_company, preprocess_company


# =============================================================================
# CONNECT
# =============================================================================

print("Connecting to Snowflake...")
conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
cursor = conn.cursor()

# =============================================================================
# LOAD CANONICAL LIST
# =============================================================================

print("Loading canonical creditors...")
cursor.execute(f"""
    SELECT CANONICAL_NAME, SOURCE_ROW_COUNT, CONFIDENCE_TIER, MATCH_SOURCE
    FROM {OUTPUT_TABLES['canonical']}
    WHERE CONFIDENCE_TIER != 'VERIFIED'
    ORDER BY SOURCE_ROW_COUNT DESC
""")
rows = cursor.fetchall()
unverified = pl.DataFrame({
    'CANONICAL_NAME': [r[0] for r in rows],
    'SOURCE_ROW_COUNT': [r[1] for r in rows],
    'CONFIDENCE_TIER': [r[2] for r in rows],
    'MATCH_SOURCE': [r[3] for r in rows],
})
print(f"  Loaded {len(unverified):,} unverified canonical names")

# Build verified set (seed canonicals + their known names)
verified_canonicals = set(CANONICAL_NAMES)
print(f"  Verified canonicals (from seed): {len(verified_canonicals):,}")


# =============================================================================
# HEURISTIC A: Known canonical as prefix
# =============================================================================
# If a VERIFIED canonical appears at the start of an unverified name,
# the unverified name is likely a variant.
# Example: "CAPITAL ONE" is prefix of "CAPITAL ONE AUTO FINAN"

print("\nHeuristic A: Known canonical as prefix...")
start = time.time()

suggestions = []

# Sort verified canonicals longest-first (match most specific first)
sorted_canonicals = sorted(verified_canonicals, key=len, reverse=True)

# Only check canonicals with 2+ words (single words like "SECURITY" are too broad)
multi_word_canonicals = [c for c in sorted_canonicals if ' ' in c]

unverified_names = unverified['CANONICAL_NAME'].to_list()
unverified_rows = dict(zip(
    unverified['CANONICAL_NAME'].to_list(),
    unverified['SOURCE_ROW_COUNT'].to_list()
))

for canonical in multi_word_canonicals:
    prefix = canonical + ' '
    for name in unverified_names:
        if name.startswith(prefix) and name != canonical:
            suggestions.append({
                'CURRENT_NAME': name,
                'SUGGESTED_CANONICAL': canonical,
                'REASON': 'prefix_match',
                'DETAIL': f'Starts with verified "{canonical}"',
                'SOURCE_ROW_COUNT': unverified_rows.get(name, 0),
            })

print(f"  Found {len(suggestions):,} prefix matches in {time.time()-start:.1f}s")


# =============================================================================
# HEURISTIC B: Slash / ampersand splitting
# =============================================================================
# Split names on / and & — if either half matches a known canonical, suggest merge.
# Example: "CAPITAL ONE/L&T" → split → "CAPITAL ONE" matches

print("\nHeuristic B: Slash/ampersand splitting...")
start = time.time()
slash_count = 0

for name in unverified_names:
    if '/' not in name and '&' not in name:
        continue

    # Split on / first, then & within each part
    parts = []
    for segment in name.split('/'):
        parts.extend(segment.split('&'))

    parts = [p.strip() for p in parts if p.strip()]

    for part in parts:
        if part in verified_canonicals:
            suggestions.append({
                'CURRENT_NAME': name,
                'SUGGESTED_CANONICAL': part,
                'REASON': 'slash_split',
                'DETAIL': f'Contains verified "{part}" after splitting on /&',
                'SOURCE_ROW_COUNT': unverified_rows.get(name, 0),
            })
            slash_count += 1
            break  # One match per name is enough

print(f"  Found {slash_count:,} slash/ampersand matches in {time.time()-start:.1f}s")


# =============================================================================
# HEURISTIC C: Concatenation detection
# =============================================================================
# For names that look like jammed-together abbreviations, try normalizing
# and see if they resolve to a known canonical.
# Example: CAPONEAUTO → normalize → CAPITAL ONE AUTO → prefix of CAPITAL ONE

print("\nHeuristic C: Concatenation / abbreviation detection...")
start = time.time()
concat_count = 0

# Focus on shorter names (likely abbreviations) that are a single token
single_token_names = [n for n in unverified_names if ' ' not in n and len(n) >= 6]

for name in single_token_names:
    # Try normalizing the jammed name — our token substitutions might catch it
    normalized = normalize_company(name)
    if normalized != name and normalized in verified_canonicals:
        suggestions.append({
            'CURRENT_NAME': name,
            'SUGGESTED_CANONICAL': normalized,
            'REASON': 'abbreviation_expansion',
            'DETAIL': f'Normalizes to verified "{normalized}"',
            'SOURCE_ROW_COUNT': unverified_rows.get(name, 0),
        })
        concat_count += 1
        continue

    # Check if normalized form starts with a known canonical
    for canonical in multi_word_canonicals:
        if normalized.startswith(canonical + ' ') or normalized == canonical:
            suggestions.append({
                'CURRENT_NAME': name,
                'SUGGESTED_CANONICAL': canonical,
                'REASON': 'abbreviation_prefix',
                'DETAIL': f'Normalizes to "{normalized}" which starts with "{canonical}"',
                'SOURCE_ROW_COUNT': unverified_rows.get(name, 0),
            })
            concat_count += 1
            break

print(f"  Found {concat_count:,} abbreviation/concatenation matches in {time.time()-start:.1f}s")


# =============================================================================
# HEURISTIC D: Unverified-to-unverified prefix (high-volume absorbs low-volume)
# =============================================================================
# If an unverified name with HIGH row count appears as a prefix of another
# unverified name with LOW row count, the high-count one is likely the canonical.
# Example: "JPMORGAN CHASE" (50K rows) is prefix of "JPMORGAN CHASE BANK AUTO" (1K rows)

print("\nHeuristic D: High-volume prefix absorbing low-volume variants...")
start = time.time()
absorb_count = 0

# Only check names with 5000+ rows as potential canonicals
high_volume = [(n, r) for n, r in unverified_rows.items()
               if r >= 5000 and ' ' in n]
high_volume.sort(key=lambda x: x[1], reverse=True)

for hv_name, hv_rows in high_volume:
    prefix = hv_name + ' '
    for name in unverified_names:
        if name.startswith(prefix) and name != hv_name:
            name_rows = unverified_rows.get(name, 0)
            # Only suggest if the variant has significantly fewer rows
            if name_rows < hv_rows * 0.5:
                suggestions.append({
                    'CURRENT_NAME': name,
                    'SUGGESTED_CANONICAL': hv_name,
                    'REASON': 'high_volume_prefix',
                    'DETAIL': f'"{hv_name}" ({hv_rows:,} rows) is prefix; this has {name_rows:,} rows',
                    'SOURCE_ROW_COUNT': name_rows,
                })
                absorb_count += 1

print(f"  Found {absorb_count:,} high-volume absorptions in {time.time()-start:.1f}s")


# =============================================================================
# DEDUPLICATE AND SORT
# =============================================================================

print(f"\nTotal raw suggestions: {len(suggestions):,}")

# Deduplicate (same CURRENT_NAME might be suggested by multiple heuristics)
seen = set()
unique_suggestions = []
for s in suggestions:
    key = s['CURRENT_NAME']
    if key not in seen:
        seen.add(key)
        unique_suggestions.append(s)

# Sort by SOURCE_ROW_COUNT descending (highest impact first)
unique_suggestions.sort(key=lambda x: x['SOURCE_ROW_COUNT'], reverse=True)

print(f"Unique suggestions (deduplicated): {len(unique_suggestions):,}")


# =============================================================================
# WRITE TO SNOWFLAKE
# =============================================================================

if unique_suggestions:
    now = datetime.now(timezone.utc).isoformat()
    for s in unique_suggestions:
        s['STATUS'] = 'pending'
        s['CREATED_AT'] = now

    df = pd.DataFrame(unique_suggestions)

    print(f"\nWriting {len(df):,} suggestions to {TARGET_SCHEMA}.CREDITOR_MERGE_SUGGESTIONS...")
    write_pandas(
        conn, df,
        table_name="CREDITOR_MERGE_SUGGESTIONS",
        database=TARGET_DATABASE, schema=TARGET_SCHEMA_NAME,
        auto_create_table=True, overwrite=True
    )
    print("  Done.")
else:
    print("\nNo suggestions found.")


# =============================================================================
# SUMMARY
# =============================================================================

print(f"\n{'='*60}")
print("AUTO-SUGGEST COMPLETE")
print(f"{'='*60}")

by_reason = {}
for s in unique_suggestions:
    reason = s['REASON']
    by_reason[reason] = by_reason.get(reason, 0) + 1

print(f"  Total suggestions: {len(unique_suggestions):,}")
for reason, count in sorted(by_reason.items(), key=lambda x: x[1], reverse=True):
    print(f"    {reason}: {count:,}")

total_rows_covered = sum(s['SOURCE_ROW_COUNT'] for s in unique_suggestions)
print(f"  Source rows covered: {total_rows_covered:,}")
print(f"\n  Output: {TARGET_SCHEMA}.CREDITOR_MERGE_SUGGESTIONS")
print(f"  Next: Review in Streamlit app or approve in bulk")

cursor.close()
conn.close()
print("\nDone.")
