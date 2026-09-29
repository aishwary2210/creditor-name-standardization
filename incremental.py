# Incremental resolver — process new creditor names against existing canonical list
#
# Finds NEW COMPANY values not yet in CREDITOR_ALIAS_MAP and resolves them.
# APPENDS to existing tables — never overwrites.
#
# Usage: python incremental.py

import time
from datetime import datetime, timezone

import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from rapidfuzz import fuzz, process

from config import (
    SNOWFLAKE_CONFIG, SOURCE_TABLE, NORMALIZED_TABLE,
    THRESHOLDS, OUTPUT_TABLES, PIPELINE_VERSION,
    TARGET_DATABASE, TARGET_SCHEMA_NAME,
)
from normalize import normalize_company

print("=" * 70)
print("CREDITOR STANDARDIZATION — INCREMENTAL RESOLVER")
print(f"Version: {PIPELINE_VERSION}")
print(f"Started: {datetime.now(timezone.utc).isoformat()}")
print("=" * 70)

# Connect
print("\nConnecting to Snowflake...")
conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
cursor = conn.cursor()
print("  Connected.")

# =============================================================================
# STEP 1: FIND NEW COMPANY VALUES NOT YET RESOLVED
# =============================================================================

print("\nStep 1: Finding new unresolved COMPANY values...")

cursor.execute(f"""
    SELECT DISTINCT cr.COMPANY
    FROM {SOURCE_TABLE} cr
    WHERE cr._FIVETRAN_DELETED = FALSE
      AND cr.COMPANY IS NOT NULL
      AND cr.COMPANY != ''
      AND cr.COMPANY NOT IN (
          SELECT RAW_COMPANY FROM {NORMALIZED_TABLE}
      )
""")

new_raw_names = [row[0] for row in cursor.fetchall()]
print(f"  Found {len(new_raw_names):,} new COMPANY values")

if not new_raw_names:
    print("  Nothing to do.")
    cursor.close()
    conn.close()
    exit(0)

# =============================================================================
# STEP 2: NORMALIZE NEW NAMES
# =============================================================================

print("\nStep 2: Normalizing...")
new_items = []
for raw in new_raw_names:
    normalized = normalize_company(raw)
    if normalized:
        new_items.append({'raw_company': raw, 'normalized_company': normalized})

print(f"  Normalized {len(new_items):,} names")

# =============================================================================
# STEP 3: LOAD EXISTING CANONICAL LIST
# =============================================================================

print("\nStep 3: Loading existing canonicals...")
cursor.execute(f"""
    SELECT CANONICAL_CREDITOR_ID, CANONICAL_NAME
    FROM {OUTPUT_TABLES['canonical']}
""")
canonical_rows = cursor.fetchall()
canonical_names = [r[1] for r in canonical_rows]
canonical_lookup = {r[1]: r[0] for r in canonical_rows}

# Also load existing alias map for exact-match
cursor.execute(f"SELECT NORMALIZED_COMPANY, CANONICAL_NAME FROM {OUTPUT_TABLES['alias_map']}")
existing_aliases = {r[0]: r[1] for r in cursor.fetchall()}

print(f"  {len(canonical_names):,} canonicals, {len(existing_aliases):,} existing aliases")

# =============================================================================
# STEP 4: RESOLVE
# =============================================================================

print("\nStep 4: Resolving new names...")
start = time.time()

new_exact = []
new_fuzzy = []
new_review = []
new_unresolved = []

for item in new_items:
    name = item['normalized_company']

    # Exact match against existing aliases or canonical names
    if name in existing_aliases:
        cname = existing_aliases[name]
        cid = canonical_lookup.get(cname)
        if cid:
            new_exact.append({**item, 'canonical_id': cid, 'canonical_name': cname})
        continue

    if name in canonical_lookup:
        new_exact.append({**item, 'canonical_id': canonical_lookup[name], 'canonical_name': name})
        continue

    # Fuzzy match
    result = process.extractOne(
        query=name, choices=canonical_names,
        scorer=fuzz.WRatio,
        score_cutoff=int(THRESHOLDS['review_min'] * 100)
    )

    if result is None:
        new_unresolved.append(item)
        continue

    match_name, score_raw, _ = result
    score = score_raw / 100.0

    if score >= THRESHOLDS['auto_accept']:
        cid = canonical_lookup[match_name]
        new_fuzzy.append({**item, 'canonical_id': cid, 'canonical_name': match_name, 'score': score})
    else:
        new_review.append({**item, 'best_match': match_name, 'score': score})

print(f"  Done in {time.time()-start:.1f}s")
print(f"    Exact:      {len(new_exact):,}")
print(f"    Fuzzy:      {len(new_fuzzy):,}")
print(f"    Review:     {len(new_review):,}")
print(f"    Unresolved: {len(new_unresolved):,}")

# =============================================================================
# STEP 5: WRITE NEW ENTRIES (APPEND)
# =============================================================================

print("\nStep 5: Appending to Snowflake...")
now = datetime.now(timezone.utc).isoformat()

# Append to CREDITORS_NORMALIZED
new_norm_records = [{'RAW_COMPANY': i['raw_company'], 'NORMALIZED_COMPANY': i['normalized_company'], 'ROW_COUNT': 1} for i in new_items]
if new_norm_records:
    write_pandas(conn, pd.DataFrame(new_norm_records),
                 table_name="CREDITORS_NORMALIZED",
                 database=TARGET_DATABASE, schema=TARGET_SCHEMA_NAME,
                 auto_create_table=False, overwrite=False)

# Append to alias map
new_alias_records = []
for item in new_exact + new_fuzzy:
    new_alias_records.append({
        'NORMALIZED_COMPANY': item['normalized_company'],
        'CANONICAL_CREDITOR_ID': item['canonical_id'],
        'CANONICAL_NAME': item['canonical_name'],
        'CONFIDENCE': item.get('score', 1.0),
        'MATCH_SOURCE': 'incremental',
        'PIPELINE_VERSION': PIPELINE_VERSION,
        'SOURCE_ROW_COUNT': 1,
        'MATCHED_AT': now,
    })

if new_alias_records:
    write_pandas(conn, pd.DataFrame(new_alias_records),
                 table_name="CREDITOR_ALIAS_MAP",
                 database=TARGET_DATABASE, schema=TARGET_SCHEMA_NAME,
                 auto_create_table=False, overwrite=False)
    print(f"  Appended {len(new_alias_records):,} to CREDITOR_ALIAS_MAP")

# Append to review queue
if new_review:
    review_recs = [{
        'NORMALIZED_COMPANY': i['normalized_company'],
        'BEST_MATCH_CANONICAL': i['best_match'],
        'SCORE': i['score'],
        'REVIEW_PRIORITY': 'incremental',
        'SOURCE_ROWS': 1,
        'IS_HIGH_VOLUME': False,
        'PIPELINE_VERSION': PIPELINE_VERSION,
        'CREATED_AT': now,
    } for i in new_review]
    write_pandas(conn, pd.DataFrame(review_recs),
                 table_name="CREDITOR_REVIEW_QUEUE",
                 database=TARGET_DATABASE, schema=TARGET_SCHEMA_NAME,
                 auto_create_table=False, overwrite=False)
    print(f"  Appended {len(review_recs):,} to CREDITOR_REVIEW_QUEUE")

print(f"\n{'='*70}")
print(f"INCREMENTAL COMPLETE")
print(f"  Resolved: {len(new_exact) + len(new_fuzzy):,} | Review: {len(new_review):,} | Unresolved: {len(new_unresolved):,}")
print(f"{'='*70}")

cursor.close()
conn.close()
