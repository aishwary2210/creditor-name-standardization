# Creditor name standardization

I built this workflow to turn inconsistent creditor names into a lookup that analysts could use in Snowflake. The source had 11.9 million records and about 720,000 distinct name strings. This repository contains sanitized versions of the VS Code scripts I used, plus a small local example. Credentials, company table names, source records, and the full curated alias list are excluded.

| File | Role |
| --- | --- |
| `main.py` | Normalize names, match known creditors, cluster remaining names, and write the canonical list, alias map, and review queue. |
| `incremental.py` | Process new source names against the existing lookup. |
| `suggest_merges.py` | Suggest additional merges for human review. |
| `review_app.py` | Review suggestions and record manual overrides in Streamlit. |
| `normalize.py`, `seed_aliases.py` | Shared name cleaning and a small fictional seed list. |
| `config.py` | Connection settings from environment variables; no credentials in the repo. |

For example, `ASTER BK` maps to `ASTER BANK` through a known alias. `Orion Fundng` is close enough to `ORION FUNDING` to be grouped in the local sample. `Aster Banc` stays separate and appears in the review queue. A suggestion is not treated as a confirmed match until a person approves it.

## Try the local sample

The small example needs only Python 3.10 or newer:

```bash
python3 creditor_standardization.py --input data/synthetic_creditors.csv --aliases data/synthetic_aliases.csv --output output
python3 -m unittest discover -s tests -v
```

It writes `alias_map.csv`, `canonical_creditors.csv`, and `review_queue.csv`. The included fictional input has 14 distinct names and 58 source rows; it produces 7 canonical names and 1 review candidate.

## Snowflake scripts

The Snowflake scripts need the packages in `requirements.txt`, a source table with `COMPANY` and `_FIVETRAN_DELETED` columns, and a schema where you can create output tables. `snowflake/example.sql` creates a fictional source table in a disposable `DEMO_DB.PUBLIC` schema. Set `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`, and `SNOWFLAKE_DATABASE`; `SNOWFLAKE_SCHEMA` and `CREDITOR_SOURCE_TABLE` are optional. Authentication defaults to browser sign-in. `SNOWFLAKE_PASSWORD` can be supplied through the environment when needed. See `config.py` before running: `main.py` replaces its output tables.

```bash
python3 -m pip install -r requirements.txt
python3 main.py
python3 incremental.py
python3 suggest_merges.py
streamlit run review_app.py
```

These scripts are provided to show the actual workflow and have not been rerun in a public Snowflake account. The local example is the runnable demonstration. Similarity scores help rank candidates; they are not measured match probabilities or an accuracy claim.
