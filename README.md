# Creditor name standardization

A small, runnable portfolio version of a creditor name resolution workflow. The included names and counts are fictional. This repository does not contain employer data, credentials, or the original production alias list.

The program cleans name variations, applies a short curated alias list, proposes similar known names for review, and groups a few remaining variants using blocking and direct comparison to a representative. It produces three CSVs: an alias map, a canonical list, and a review queue. A review suggestion stays separate until a person confirms it.

## Run locally

Python 3.10 or newer is enough; there are no third party dependencies.

```bash
python3 creditor_standardization.py \
  --input data/synthetic_creditors.csv \
  --aliases data/synthetic_aliases.csv \
  --output output
python3 -m unittest discover -s tests -v
```

Input names use `raw_name,row_count`. The alias file uses `alias,canonical_name`. Each valid distinct raw name has exactly one row in `alias_map.csv`, and `source_rows` reconcile across the input, alias map, and canonical output. Blank names and invalid counts fail validation rather than silently disappearing.

## Matching decisions

1. Normalize case, spacing, punctuation, selected short forms, and legal suffixes.
2. Resolve known aliases exactly.
3. Compare remaining names with known canonicals. Clear matches are assigned; borderline matches go to `review_queue.csv` and retain their own canonical name.
4. Use token, phonetic, and edge keys to propose candidates among remaining names. A cluster member must directly pass the overlap, length, and similarity checks against its representative.
5. Keep all other names as standalone entities.

Similarity scores are heuristics, not measured probabilities of a correct match. The sample is for inspecting the workflow; it does not establish accuracy, throughput, or behavior at production scale. The Snowflake query in [`snowflake/example.sql`](snowflake/example.sql) shows how an exported alias map could be joined after loading it to a demo schema; it has not been executed against Snowflake.

## Public scope

The source work involved Snowflake creditor data. This independent portfolio implementation uses a short fictional seed list and a local CSV so the matching decisions and review boundary can be examined without access to that environment.
