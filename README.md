# Creditor name standardization

At work, I built a Snowflake creditor lookup to make inconsistent names usable in downstream analysis. This repo shows the matching approach with fictional names and a short fictional alias list. It contains no company code or data.

| Input | Output | Decision |
| --- | --- | --- |
| `ASTER BK` | `ASTER BANK` | Known alias |
| `Orion Fundng` | `ORION FUNDING` | Similar name in the same candidate group |
| `Aster Banc` | `ASTER BANC` | Kept separate and sent for review |

The resolver first normalizes names and checks known aliases. It then scores possible matches to known creditors. Names that are close but uncertain stay separate in the review queue. For remaining names, it uses token, phonetic, and prefix/suffix keys to find candidates; each accepted cluster member must match its representative directly. Other names remain standalone.

## Run the example

Python 3.10 or newer; no packages to install.

```bash
python3 creditor_standardization.py \
  --input data/synthetic_creditors.csv \
  --aliases data/synthetic_aliases.csv \
  --output output
python3 -m unittest discover -s tests -v
```

The run writes `alias_map.csv`, `canonical_creditors.csv`, and `review_queue.csv`. With the included sample, 14 distinct input names map to 7 canonical names, with 1 review candidate. The 58 source rows reconcile across the input and outputs.

This is a small public demonstration, not the production Snowflake pipeline. Similarity scores are ranking signals, not measured match probabilities or an accuracy claim.
