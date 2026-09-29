# Creditor Standardization Pipeline — Main script (COMPANY name only)
#
# Self-contained pipeline that:
#   Step 0: Builds CREDITORS_NORMALIZED from source (pulls + normalizes COMPANY)
#   Step 1: Connects to Snowflake
#   Step 2: Loads the curated seed list
#   Step 3: Exact match normalized names against seed
#   Step 4: Fuzzy match remaining names (rapidfuzz)
#   Step 5: Build CANONICAL_CREDITORS table
#   Step 6: Build CREDITOR_ALIAS_MAP table
#   Step 7: Build CREDITOR_REVIEW_QUEUE table
#   Step 8: Write everything to Snowflake
#
# Usage:
#   pip install -r requirements.txt
#   python main.py
#
# Only processes COMPANY names — no address, phone, or other fields.

import time
from datetime import datetime, timezone

import polars as pl
import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from rapidfuzz import fuzz, process
import jellyfish

from config import (
    SNOWFLAKE_CONFIG, SOURCE_TABLE, NORMALIZED_TABLE, TARGET_SCHEMA,
    THRESHOLDS, HIGH_VOLUME_THRESHOLD, MIN_TOKEN_OVERLAP, MAX_LENGTH_RATIO,
    MAX_BLOCK_SIZE, CONFIDENCE_TIERS, OUTPUT_TABLES, PIPELINE_VERSION,
    TARGET_DATABASE, TARGET_SCHEMA_NAME,
)
from normalize import normalize_company, preprocess_company
from seed_aliases import ALIAS_MAP, CANONICAL_NAMES, lookup as seed_lookup

print("=" * 70)
print("CREDITOR STANDARDIZATION PIPELINE")
print(f"Version: {PIPELINE_VERSION}")
print(f"Started: {datetime.now(timezone.utc).isoformat()}")
print("=" * 70)


# =============================================================================
# STEP 0: CONNECT TO SNOWFLAKE
# =============================================================================

print("\nStep 0: Connecting to Snowflake...")
conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
cursor = conn.cursor()
print("  Connected.")


# =============================================================================
# STEP 1: BUILD CREDITORS_NORMALIZED TABLE
# =============================================================================
# Pull all distinct COMPANY values from the source table, normalize them,
# and write to CREDITORS_NORMALIZED. This makes the pipeline self-contained.

print("\nStep 1: Building CREDITORS_NORMALIZED from source...")
start = time.time()

# Pull distinct COMPANY + count from source
print("  Pulling distinct COMPANY values from CREDITORS...")
cursor.execute(f"""
    SELECT COMPANY, COUNT(*) AS ROW_COUNT
    FROM {SOURCE_TABLE}
    WHERE COMPANY IS NOT NULL AND COMPANY != ''
      AND _FIVETRAN_DELETED = FALSE
    GROUP BY COMPANY
    ORDER BY ROW_COUNT DESC
""")

raw_rows = cursor.fetchall()
print(f"  Fetched {len(raw_rows):,} distinct raw COMPANY strings")

# Normalize each company name using Python
print("  Normalizing company names...")
normalized_records = []
for raw_company, row_count in raw_rows:
    preprocessed = preprocess_company(raw_company)
    normalized = normalize_company(raw_company)
    if normalized:
        normalized_records.append({
            'RAW_COMPANY': raw_company,
            'PREPROCESSED_COMPANY': preprocessed,
            'NORMALIZED_COMPANY': normalized,
            'ROW_COUNT': row_count,
        })

norm_pandas_df = pd.DataFrame(normalized_records)

# Write to Snowflake
print(f"  Writing {len(norm_pandas_df):,} rows to {NORMALIZED_TABLE}...")
write_pandas(
    conn, norm_pandas_df,
    table_name="CREDITORS_NORMALIZED",
    database=TARGET_DATABASE, schema=TARGET_SCHEMA_NAME,
    auto_create_table=True, overwrite=True
)

n_raw = len(raw_rows)
n_normalized = norm_pandas_df['NORMALIZED_COMPANY'].nunique()
print(f"  Done in {time.time()-start:.1f}s")
print(f"  Raw distinct: {n_raw:,} → Normalized distinct: {n_normalized:,}")
print(f"  Reduction: {n_raw - n_normalized:,} collapsed ({(1 - n_normalized/n_raw)*100:.1f}%)")


# =============================================================================
# STEP 2: LOAD NORMALIZED DATA (grouped by normalized name)
# =============================================================================

print("\nStep 2: Loading normalized data (grouped)...")

cursor.execute(f"""
    SELECT NORMALIZED_COMPANY, SUM(ROW_COUNT) AS TOTAL_ROWS
    FROM {NORMALIZED_TABLE}
    WHERE NORMALIZED_COMPANY IS NOT NULL AND NORMALIZED_COMPANY != ''
    GROUP BY NORMALIZED_COMPANY
    ORDER BY TOTAL_ROWS DESC
""")

rows = cursor.fetchall()
norm_df = pl.DataFrame({
    'NORMALIZED_COMPANY': [r[0] for r in rows],
    'TOTAL_ROWS': [r[1] for r in rows],
})

all_names = norm_df['NORMALIZED_COMPANY'].to_list()
print(f"  {len(all_names):,} distinct normalized names")


# =============================================================================
# STEP 3: LOAD SEED LIST (from curated alias map)
# =============================================================================

print("\nStep 3: Loading curated seed list...")

seed_names = CANONICAL_NAMES  # All uppercase canonical names
known_aliases = ALIAS_MAP     # All uppercase alias -> canonical mapping

print(f"  Canonical creditors: {len(seed_names):,}")
print(f"  Known aliases: {len(known_aliases):,}")


# =============================================================================
# STEP 3.5: LOAD MANUAL OVERRIDES (from Streamlit review app)
# =============================================================================
# Human-approved mappings stored in Snowflake. These take priority over
# clustering — if a human said "X = Y", that's the final answer.

print("\nStep 3.5: Loading manual overrides...")

manual_overrides = {}
try:
    cursor.execute(f"""
        SELECT ORIGINAL_NAME, CANONICAL_NAME
        FROM {TARGET_SCHEMA}.CREDITOR_MANUAL_OVERRIDES
    """)
    override_rows = cursor.fetchall()
    for orig, canon in override_rows:
        manual_overrides[orig] = canon
    print(f"  Loaded {len(manual_overrides):,} manual overrides")
except Exception:
    print("  No manual overrides table found (first run). Skipping.")


# =============================================================================
# STEP 4: EXACT MATCH
# =============================================================================

print("\nStep 4: Exact matching against seed list + manual overrides...")

# Build lookup: alias map + canonical names + manual overrides
alias_lookup = known_aliases.copy()
for name in seed_names:
    alias_lookup[name] = name
# Manual overrides take highest priority
for orig, canon in manual_overrides.items():
    alias_lookup[orig] = canon
    # Ensure override canonicals appear in seed_names
    if canon not in seed_names:
        seed_names = list(seed_names) + [canon] if not isinstance(seed_names, list) else seed_names
        if canon not in seed_names:
            seed_names.append(canon)

# Build a map of normalized_name -> list of preprocessed forms (for alias lookup)
# Multiple raw strings can normalize to the same thing; their preprocessed forms
# may differ and some may hit the alias map even if the normalized form doesn't.
norm_to_preprocessed = {}
for rec in normalized_records:
    norm = rec['NORMALIZED_COMPANY']
    pre = rec['PREPROCESSED_COMPANY']
    if norm not in norm_to_preprocessed:
        norm_to_preprocessed[norm] = set()
    norm_to_preprocessed[norm].add(pre)

exact_matches = {}  # normalized_name -> canonical_name
unmatched = []

for name in all_names:
    # 1. Try the fully-normalized name against alias map
    if name in alias_lookup:
        exact_matches[name] = alias_lookup[name]
        continue

    # 2. Try the preprocessed forms (before token expansion/legal stripping)
    matched = False
    for preprocessed in norm_to_preprocessed.get(name, []):
        if preprocessed in alias_lookup:
            exact_matches[name] = alias_lookup[preprocessed]
            matched = True
            break
        result = seed_lookup(preprocessed)
        if result:
            exact_matches[name] = result
            matched = True
            break

    if not matched:
        # 3. Try fallback (trailing punctuation strip on normalized)
        result = seed_lookup(name)
        if result:
            exact_matches[name] = result
        else:
            unmatched.append(name)

print(f"  Exact matches: {len(exact_matches):,}")
print(f"  Remaining for fuzzy: {len(unmatched):,}")


# =============================================================================
# STEP 5: FUZZY MATCHING AGAINST SEED (high cutoff — only genuine matches)
# =============================================================================

print(f"\nStep 5: Fuzzy matching {len(unmatched):,} names against {len(seed_names):,} seed canonicals...")
start = time.time()

fuzzy_accepted = {}   # normalized_name -> canonical_name (auto-accept)
fuzzy_review = []     # pairs for human review
no_match = []         # names that don't match any seed canonical

for idx, name in enumerate(unmatched):
    result = process.extractOne(
        query=name,
        choices=seed_names,
        scorer=fuzz.WRatio,
        score_cutoff=int(THRESHOLDS['review_min'] * 100)
    )

    if result is None:
        # No seed canonical is similar enough — this is a standalone/cluster candidate
        no_match.append(name)
        continue

    match_name, score_raw, _ = result
    score = score_raw / 100.0

    # High-volume safety: if both exceed the configured row count, force review.
    name_rows = norm_df.filter(pl.col('NORMALIZED_COMPANY') == name)['TOTAL_ROWS'][0]
    match_rows = norm_df.filter(pl.col('NORMALIZED_COMPANY') == match_name)['TOTAL_ROWS']
    match_row_count = int(match_rows[0]) if len(match_rows) > 0 else 0
    is_high_volume = (int(name_rows) > HIGH_VOLUME_THRESHOLD and
                      match_row_count > HIGH_VOLUME_THRESHOLD)

    if score >= THRESHOLDS['auto_accept'] and not is_high_volume:
        fuzzy_accepted[name] = match_name
    else:
        # Review range or high volume: leave the names separate for now.
        fuzzy_review.append({
            'normalized_company': name,
            'best_match': match_name,
            'score': round(score, 4),
            'source_rows': int(name_rows),
            'is_high_volume': is_high_volume,
        })

    if (idx + 1) % 10000 == 0:
        elapsed = time.time() - start
        remaining = (len(unmatched) - idx - 1) / ((idx + 1) / elapsed)
        print(f"    {idx+1:,}/{len(unmatched):,} (~{remaining:.0f}s remaining)")

print(f"\n  Done in {time.time()-start:.1f}s")
print(f"    Auto-accept (seed match):  {len(fuzzy_accepted):,}")
print(f"    Review (potential match):   {len(fuzzy_review):,}")
print(f"    No match (cluster/standalone): {len(no_match):,}")


# =============================================================================
# STEP 5.5: CLUSTER UNMATCHED NAMES (multi-pass blocking)
# =============================================================================
# Names that didn't match any seed canonical are compared AGAINST EACH OTHER
# to find natural groups (e.g., "NAVIENT" + "NAVIENT SOLUTIONS" = same entity).
#
# Techniques used:
#   - Multi-pass blocking (token, phonetic, prefix-suffix)
#   - Length ratio filter (prevents "GREEN" merging with "GREEN DOT BANK")
#   - Token overlap pre-filter
#   - Hybrid scorer: Jaro-Winkler for short names, token_sort_ratio for longer
#   - Star pattern: each member must match representative directly (no chaining)

print(f"\nStep 5.5: Clustering {len(no_match):,} unmatched names (multi-pass blocking)...")
start = time.time()

STOP_WORDS = {'THE', 'A', 'AN', 'OF', 'AND', 'FOR', 'IN', 'AT', 'BY', 'TO', 'OR'}

# ─── Build row count lookup ─────────────────────────────────────────────────
name_to_rows = {}
for name in no_match:
    rows = norm_df.filter(pl.col('NORMALIZED_COMPANY') == name)['TOTAL_ROWS']
    name_to_rows[name] = int(rows[0]) if len(rows) > 0 else 0


# ─── Multi-pass blocking functions ──────────────────────────────────────────

def get_significant_tokens(name):
    """Extract meaningful tokens from a name (skip stop words, numbers, single chars)."""
    return [t for t in name.split()
            if t not in STOP_WORDS and len(t) > 1 and not t.isdigit()]

def block_key_token(name):
    """Pass 1: First two significant tokens."""
    tokens = get_significant_tokens(name)
    if len(tokens) >= 2:
        return tokens[0] + '_' + tokens[1][:4]
    elif tokens and len(tokens[0]) >= 4:
        return tokens[0]
    return None

def block_key_phonetic(name):
    """Pass 2: Metaphone code of first significant word (catches typo variants)."""
    tokens = get_significant_tokens(name)
    if tokens and len(tokens[0]) >= 3:
        try:
            code = jellyfish.metaphone(tokens[0])
            return f"PH_{code}" if code else None
        except Exception:
            return None
    return None

def block_key_prefix_suffix(name):
    """Pass 3: First 4 + last 4 chars (catches truncated names)."""
    clean = name.replace(' ', '')
    if len(clean) >= 8:
        return f"PS_{clean[:4]}_{clean[-4:]}"
    elif len(clean) >= 4:
        return f"PS_{clean[:4]}"
    return None


# Build blocks using all three passes (union of candidates)
candidate_pairs = set()  # set of (name_a, name_b) pairs to compare

# Collect all blocks from all passes
all_blocks = {}  # block_key -> set of names

for name in no_match:
    for key_fn in [block_key_token, block_key_phonetic, block_key_prefix_suffix]:
        key = key_fn(name)
        if key:
            if key not in all_blocks:
                all_blocks[key] = []
            all_blocks[key].append(name)

print(f"  Multi-pass blocks: {len(all_blocks):,}")


# ─── Pre-filters and scoring ────────────────────────────────────────────────

def token_overlap_ratio(name_a, name_b):
    """Fraction of shared tokens between two names."""
    tokens_a = set(get_significant_tokens(name_a))
    tokens_b = set(get_significant_tokens(name_b))
    if not tokens_a or not tokens_b:
        return 0.0
    shared = tokens_a & tokens_b
    return len(shared) / min(len(tokens_a), len(tokens_b))

def length_ratio(name_a, name_b):
    """Ratio of longer string to shorter string."""
    la, lb = len(name_a), len(name_b)
    if la == 0 or lb == 0:
        return 999.0
    return max(la, lb) / min(la, lb)

def hybrid_score(name_a, name_b):
    """
    Hybrid scorer:
    - Short names (both <= 20 chars): Jaro-Winkler (better for prefix similarity)
    - Longer names: token_sort_ratio (better for multi-word comparison)
    Returns score 0-100.
    """
    if len(name_a) <= 20 and len(name_b) <= 20:
        # Jaro-Winkler returns 0-1, convert to 0-100
        return jellyfish.jaro_winkler_similarity(name_a, name_b) * 100
    else:
        return fuzz.token_sort_ratio(name_a, name_b)


# ─── Clustering with star pattern ───────────────────────────────────────────

cluster_threshold = THRESHOLDS['cluster_merge'] * 100
clusters = {}  # representative -> list of members
all_clustered = set()
merge_count = 0
blocks_processed = 0

for block_key, block_names in all_blocks.items():
    if len(block_names) < 2:
        continue

    # Cap block size (sort by frequency, keep top N)
    if len(block_names) > MAX_BLOCK_SIZE:
        block_names = sorted(block_names, key=lambda n: name_to_rows.get(n, 0), reverse=True)[:MAX_BLOCK_SIZE]

    # Skip names already clustered (from a previous block pass)
    available = [n for n in block_names if n not in all_clustered]
    if len(available) < 2:
        continue

    # Sort by frequency — highest becomes representative candidate
    sorted_names = sorted(available, key=lambda n: name_to_rows.get(n, 0), reverse=True)
    rep = sorted_names[0]

    # If rep is already a cluster representative, add to existing cluster
    if rep in clusters:
        existing_members = clusters[rep]
    else:
        existing_members = []

    new_members = []
    for other in sorted_names[1:]:
        if other in all_clustered:
            continue

        # Pre-filter 1: Length ratio check
        if length_ratio(rep, other) > MAX_LENGTH_RATIO:
            continue

        # Pre-filter 2: Token overlap check
        if token_overlap_ratio(rep, other) < MIN_TOKEN_OVERLAP:
            continue

        # Score using hybrid scorer
        score = hybrid_score(rep, other)
        if score >= cluster_threshold:
            new_members.append(other)
            merge_count += 1

    if new_members:
        clusters[rep] = existing_members + new_members
        all_clustered.add(rep)
        all_clustered.update(new_members)

    blocks_processed += 1
    if blocks_processed % 10000 == 0:
        print(f"    Processed {blocks_processed:,} blocks...")

# Second pass: form sub-clusters from leftovers in large blocks
sub_cluster_count = 0
for block_key, block_names in all_blocks.items():
    if len(block_names) < 3:
        continue

    leftovers = [n for n in block_names if n not in all_clustered]
    if len(leftovers) < 2:
        continue

    sorted_left = sorted(leftovers, key=lambda n: name_to_rows.get(n, 0), reverse=True)
    sub_rep = sorted_left[0]
    sub_members = []

    for other in sorted_left[1:]:
        if other in all_clustered:
            continue
        if length_ratio(sub_rep, other) > MAX_LENGTH_RATIO:
            continue
        if token_overlap_ratio(sub_rep, other) < MIN_TOKEN_OVERLAP:
            continue
        score = hybrid_score(sub_rep, other)
        if score >= cluster_threshold:
            sub_members.append(other)
            merge_count += 1

    if sub_members:
        clusters[sub_rep] = sub_members
        all_clustered.add(sub_rep)
        all_clustered.update(sub_members)
        sub_cluster_count += 1

print(f"  Merges found: {merge_count:,} (including {sub_cluster_count:,} sub-clusters)")

# ─── Classify all names into tiers ──────────────────────────────────────────

cluster_reps = clusters
standalone_high = []   # >= 10 rows, not clustered
standalone_low = []    # < 10 rows, not clustered (low frequency)

for name in no_match:
    if name not in all_clustered:
        if name_to_rows.get(name, 0) >= 10:
            standalone_high.append(name)
        else:
            standalone_low.append(name)

multi_cluster_count = len(cluster_reps)
clustered_names = sum(len(v) + 1 for v in cluster_reps.values())

print(f"  Done in {time.time()-start:.1f}s")
print(f"    Multi-name clusters: {multi_cluster_count:,} (containing {clustered_names:,} names)")
print(f"    Standalone (10+ rows): {len(standalone_high):,}")
print(f"    Low-frequency (< 10 rows): {len(standalone_low):,}")
print(f"    Total accounted for: {clustered_names + len(standalone_high) + len(standalone_low):,}")


# =============================================================================
# STEP 6: BUILD CANONICAL_CREDITORS (all 4 tiers — nothing discarded)
# =============================================================================

print("\nStep 6: Building CANONICAL_CREDITORS...")

canonical_records = []
canonical_id = 1

# Tier 1: Seed names (verified)
for name in seed_names:
    row_data = norm_df.filter(pl.col('NORMALIZED_COMPANY') == name)
    total_rows = int(row_data['TOTAL_ROWS'][0]) if len(row_data) > 0 else 0
    canonical_records.append({
        'CANONICAL_CREDITOR_ID': canonical_id,
        'CANONICAL_NAME': name,
        'SOURCE_ROW_COUNT': total_rows,
        'IS_VERIFIED': True,
        'CONFIDENCE_TIER': CONFIDENCE_TIERS['verified'],
        'MATCH_SOURCE': 'seed',
    })
    canonical_id += 1

# Tier 2: Cluster representatives (unverified, grouped)
for rep, members in cluster_reps.items():
    total_rows = name_to_rows.get(rep, 0)
    for m in members:
        total_rows += name_to_rows.get(m, 0)
    canonical_records.append({
        'CANONICAL_CREDITOR_ID': canonical_id,
        'CANONICAL_NAME': rep,
        'SOURCE_ROW_COUNT': total_rows,
        'IS_VERIFIED': False,
        'CONFIDENCE_TIER': CONFIDENCE_TIERS['clustered'],
        'MATCH_SOURCE': 'cluster',
    })
    canonical_id += 1

# Tier 3: Standalone high-frequency (10+ rows)
for name in standalone_high:
    canonical_records.append({
        'CANONICAL_CREDITOR_ID': canonical_id,
        'CANONICAL_NAME': name,
        'SOURCE_ROW_COUNT': name_to_rows.get(name, 0),
        'IS_VERIFIED': False,
        'CONFIDENCE_TIER': CONFIDENCE_TIERS['standalone'],
        'MATCH_SOURCE': 'standalone',
    })
    canonical_id += 1

# Tier 4: Low-frequency names (< 10 rows — kept, labeled for review)
for name in standalone_low:
    canonical_records.append({
        'CANONICAL_CREDITOR_ID': canonical_id,
        'CANONICAL_NAME': name,
        'SOURCE_ROW_COUNT': name_to_rows.get(name, 0),
        'IS_VERIFIED': False,
        'CONFIDENCE_TIER': CONFIDENCE_TIERS['low_frequency'],
        'MATCH_SOURCE': 'low_frequency',
    })
    canonical_id += 1

canonical_df = pl.DataFrame(canonical_records)
print(f"  Canonical entities: {len(canonical_df):,}")
print(f"    Tier 1 - Verified (seed):     {sum(1 for r in canonical_records if r['CONFIDENCE_TIER'] == 'VERIFIED'):,}")
print(f"    Tier 2 - Clustered:           {sum(1 for r in canonical_records if r['CONFIDENCE_TIER'] == 'CLUSTERED'):,}")
print(f"    Tier 3 - Standalone (10+):    {sum(1 for r in canonical_records if r['CONFIDENCE_TIER'] == 'STANDALONE'):,}")
print(f"    Tier 4 - Low-frequency (<10): {sum(1 for r in canonical_records if r['CONFIDENCE_TIER'] == 'LOW_FREQUENCY'):,}")


# =============================================================================
# STEP 7: BUILD CREDITOR_ALIAS_MAP
# =============================================================================

print("\nStep 7: Building CREDITOR_ALIAS_MAP...")

canonical_lookup = {r['CANONICAL_NAME']: r['CANONICAL_CREDITOR_ID'] for r in canonical_records}
now = datetime.now(timezone.utc).isoformat()

alias_records = []

# From exact matches (seed aliases)
for name, canonical_name in exact_matches.items():
    cid = canonical_lookup.get(canonical_name)
    if cid:
        row_data = norm_df.filter(pl.col('NORMALIZED_COMPANY') == name)
        alias_records.append({
            'NORMALIZED_COMPANY': name,
            'CANONICAL_CREDITOR_ID': cid,
            'CANONICAL_NAME': canonical_name,
            'CONFIDENCE': 1.0,
            'CONFIDENCE_TIER': CONFIDENCE_TIERS['verified'],
            'MATCH_SOURCE': 'exact',
            'PIPELINE_VERSION': PIPELINE_VERSION,
            'SOURCE_ROW_COUNT': int(row_data['TOTAL_ROWS'][0]) if len(row_data) > 0 else 0,
            'MATCHED_AT': now,
        })

# From fuzzy matches (auto-accept against seed)
for name, canonical_name in fuzzy_accepted.items():
    cid = canonical_lookup.get(canonical_name)
    if cid:
        row_data = norm_df.filter(pl.col('NORMALIZED_COMPANY') == name)
        alias_records.append({
            'NORMALIZED_COMPANY': name,
            'CANONICAL_CREDITOR_ID': cid,
            'CANONICAL_NAME': canonical_name,
            'CONFIDENCE': 0.95,
            'CONFIDENCE_TIER': CONFIDENCE_TIERS['seed_fuzzy'],
            'MATCH_SOURCE': 'fuzzy_seed',
            'PIPELINE_VERSION': PIPELINE_VERSION,
            'SOURCE_ROW_COUNT': int(row_data['TOTAL_ROWS'][0]) if len(row_data) > 0 else 0,
            'MATCHED_AT': now,
        })

# From cluster members (mapped to their cluster representative)
for rep, members in cluster_reps.items():
    cid = canonical_lookup.get(rep)
    if cid:
        # The representative itself
        alias_records.append({
            'NORMALIZED_COMPANY': rep,
            'CANONICAL_CREDITOR_ID': cid,
            'CANONICAL_NAME': rep,
            'CONFIDENCE': 1.0,
            'CONFIDENCE_TIER': CONFIDENCE_TIERS['clustered'],
            'MATCH_SOURCE': 'cluster_rep',
            'PIPELINE_VERSION': PIPELINE_VERSION,
            'SOURCE_ROW_COUNT': name_to_rows.get(rep, 0),
            'MATCHED_AT': now,
        })
        # Each member
        for member in members:
            alias_records.append({
                'NORMALIZED_COMPANY': member,
                'CANONICAL_CREDITOR_ID': cid,
                'CANONICAL_NAME': rep,
                'CONFIDENCE': 0.90,
                'CONFIDENCE_TIER': CONFIDENCE_TIERS['clustered'],
                'MATCH_SOURCE': 'cluster_member',
                'PIPELINE_VERSION': PIPELINE_VERSION,
                'SOURCE_ROW_COUNT': name_to_rows.get(member, 0),
                'MATCHED_AT': now,
            })

# Standalone high-frequency map to themselves
for name in standalone_high:
    cid = canonical_lookup.get(name)
    if cid:
        alias_records.append({
            'NORMALIZED_COMPANY': name,
            'CANONICAL_CREDITOR_ID': cid,
            'CANONICAL_NAME': name,
            'CONFIDENCE': 1.0,
            'CONFIDENCE_TIER': CONFIDENCE_TIERS['standalone'],
            'MATCH_SOURCE': 'standalone',
            'PIPELINE_VERSION': PIPELINE_VERSION,
            'SOURCE_ROW_COUNT': name_to_rows.get(name, 0),
            'MATCHED_AT': now,
        })

# Low-frequency standalone map to themselves
for name in standalone_low:
    cid = canonical_lookup.get(name)
    if cid:
        alias_records.append({
            'NORMALIZED_COMPANY': name,
            'CANONICAL_CREDITOR_ID': cid,
            'CANONICAL_NAME': name,
            'CONFIDENCE': 0.5,
            'CONFIDENCE_TIER': CONFIDENCE_TIERS['low_frequency'],
            'MATCH_SOURCE': 'low_frequency',
            'PIPELINE_VERSION': PIPELINE_VERSION,
            'SOURCE_ROW_COUNT': name_to_rows.get(name, 0),
            'MATCHED_AT': now,
        })

print(f"  Alias map entries: {len(alias_records):,}")
print(f"    Exact (seed):       {sum(1 for r in alias_records if r['MATCH_SOURCE'] == 'exact'):,}")
print(f"    Fuzzy (seed):       {sum(1 for r in alias_records if r['MATCH_SOURCE'] == 'fuzzy_seed'):,}")
print(f"    Cluster reps:       {sum(1 for r in alias_records if r['MATCH_SOURCE'] == 'cluster_rep'):,}")
print(f"    Cluster members:    {sum(1 for r in alias_records if r['MATCH_SOURCE'] == 'cluster_member'):,}")
print(f"    Standalone (10+):   {sum(1 for r in alias_records if r['MATCH_SOURCE'] == 'standalone'):,}")
print(f"    Low-frequency (<10):{sum(1 for r in alias_records if r['MATCH_SOURCE'] == 'low_frequency'):,}")


# =============================================================================
# STEP 8: BUILD REVIEW QUEUE (only genuine ambiguous cases)
# =============================================================================

print("\nStep 8: Building CREDITOR_REVIEW_QUEUE...")

review_records = []
for item in fuzzy_review:
    review_records.append({
        'NORMALIZED_COMPANY': item['normalized_company'],
        'BEST_MATCH_CANONICAL': item['best_match'],
        'SCORE': item['score'],
        'REVIEW_PRIORITY': 'high_volume' if item['is_high_volume'] else 'potential_match',
        'SOURCE_ROWS': item['source_rows'],
        'IS_HIGH_VOLUME': item['is_high_volume'],
        'PIPELINE_VERSION': PIPELINE_VERSION,
        'CREATED_AT': now,
    })

print(f"  Review queue: {len(review_records):,} pairs")


# =============================================================================
# STEP 9: WRITE TO SNOWFLAKE
# =============================================================================

print("\nStep 9: Writing to Snowflake...")

print(f"  Writing {OUTPUT_TABLES['canonical']}...")
write_pandas(
    conn, canonical_df.to_pandas(),
    table_name="CANONICAL_CREDITORS",
    database=TARGET_DATABASE, schema=TARGET_SCHEMA_NAME,
    auto_create_table=True, overwrite=True
)

print(f"  Writing {OUTPUT_TABLES['alias_map']}...")
alias_pandas = pd.DataFrame(alias_records)
write_pandas(
    conn, alias_pandas,
    table_name="CREDITOR_ALIAS_MAP",
    database=TARGET_DATABASE, schema=TARGET_SCHEMA_NAME,
    auto_create_table=True, overwrite=True
)

if review_records:
    print(f"  Writing {OUTPUT_TABLES['review_queue']}...")
    write_pandas(
        conn, pd.DataFrame(review_records),
        table_name="CREDITOR_REVIEW_QUEUE",
        database=TARGET_DATABASE, schema=TARGET_SCHEMA_NAME,
        auto_create_table=True, overwrite=True
    )


# =============================================================================
# STEP 10: SUMMARY
# =============================================================================

total_source = norm_df['TOTAL_ROWS'].sum()
resolved_rows = alias_pandas['SOURCE_ROW_COUNT'].sum() if len(alias_records) > 0 else 0
coverage = (resolved_rows / total_source * 100) if total_source > 0 else 0

print(f"\n{'='*70}")
print("PIPELINE COMPLETE")
print(f"{'='*70}")
print(f"""
    Source rows:           {total_source:>12,}
    Distinct normalized:   {len(all_names):>12,}
    Canonical entities:    {len(canonical_df):>12,}
    Alias map entries:     {len(alias_records):>12,}
    Review queue:          {len(review_records):>12,}
    Coverage:              {coverage:>11.1f}%

    Output:
      {OUTPUT_TABLES['canonical']}
      {OUTPUT_TABLES['alias_map']}
      {OUTPUT_TABLES['review_queue']}

    Version: {PIPELINE_VERSION}
    Completed: {datetime.now(timezone.utc).isoformat()}
""")

cursor.close()
conn.close()
print("Done.")
