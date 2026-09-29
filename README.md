# Creditor Name Standardization

Creditor names in our data were entered dozens of different ways. `CAP1`, `08 CAPITAL ONE`,
`Capital One N.A.` and `CAPITAL ONE BANK USA` are the same company, but a `GROUP BY` treats
them as four. I built this pipeline at work to turn 11.9 million creditor records (about 720,000
distinct name strings) into a lookup table in Snowflake, so reporting and modeling could join on
one canonical creditor.

This repo is a cleaned-up version of that code. Credentials, company table names and our curated
alias list are not included, and the sample data is made up.

## How it works

1. **Clean the name** ([normalize.py](normalize.py)). Uppercase, strip bureau prefixes
   (`08 `), legal suffixes (`LLC`, `N.A.`), account-type codes (`[IA]`) and `X DBA` prefixes,
   then expand abbreviations (`BK` → `BANK`, `SYNCB` → `SYNCHRONY BANK`).
2. **Look it up** in a curated alias list, plus any merges already approved in the review app.
3. **Fuzzy match** the rest against known creditors with RapidFuzz. A score of 95+ is accepted,
   93–95 goes to a review queue, and if both names have 5,000+ rows a person always decides.
4. **Cluster what's left.** Comparing every pair of 720K names would be about 259 billion
   comparisons, so names are only compared if they share a block key: the first word plus the
   start of the second, a phonetic code of the first word, or the first and last four letters. Pairs also have to share
   half their words, be within 2x in length, and score 90+ (Jaro-Winkler for short names, token
   sort for longer ones). Each member has to match the group's most common name directly, so
   groups can't chain together.
5. **Keep everything.** Names that match nothing become their own creditor, labeled `STANDALONE`
   (10+ rows) or `LOW_FREQUENCY`.
6. **Review.** [suggest_merges.py](suggest_merges.py) finds leftovers that probably belong to a
   known creditor (`ASTER BANK AUTO FINANCE` → `ASTER BANK`), and [review_app.py](review_app.py)
   is a Streamlit page for approving or rejecting them. Approvals apply on the next run.

The matching code lives in [matching.py](matching.py) and doesn't know about Snowflake, so the
local runner and the Snowflake scripts use exactly the same logic.

## Results on the production data

- 11.9M rows and about 720K distinct names resolved into a canonical creditor lookup
- Blocking cut candidate comparisons from about 259 billion possible pairs to about 10 million;
  a full run took 5–15 minutes
- About 800 names went to the first review queue
- The first merge-suggestion run found 19,906 suggestions covering 317K source rows

## Try it locally

Needs Python 3.10 or newer. No Snowflake account required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install cleanco jellyfish rapidfuzz
python run_local.py
python -m unittest discover -s tests
```

`run_local.py` reads [data/sample_creditors.csv](data/sample_creditors.csv) and
[data/sample_aliases.csv](data/sample_aliases.csv) and writes the same tables the Snowflake run
builds, as CSV files in `output/`. A few of the results:

| Source name | Canonical | Tier | Score |
| --- | --- | --- | --- |
| `ASTER BK N.A.` | ASTER BANK | VERIFIED | |
| `Riverstone Collections DBA NWC` | NORTHWIND CAPITAL | VERIFIED | |
| `Blue Harbour Credit` | BLUE HARBOR CREDIT | FUZZY | 97.3 |
| `Northwind Capitol` | NORTHWIND CAPITOL | REVIEW | 94.1 |
| `Orion Fundng` | ORION FUNDING | CLUSTERED | 98.5 |
| `River Bend Lending` | RIVERBEND LENDING | CLUSTERED | 96.5 |
| `Green Lawn Fertilizing` | GREEN LAWN FERTILIZING | LOW_FREQUENCY | |

`Northwind Capitol` is close to a known creditor but not close enough to merge on its own, so it
stays separate and shows up in the review queue. `Green Lawn Fertilizing` and `Green Dot Bank`
share a phonetic block, so they get compared, but they fail the word-overlap check.

## Run against Snowflake

```bash
pip install -r requirements.txt
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_WAREHOUSE=... SNOWFLAKE_ROLE=...
python main.py            # full rebuild; replaces the output tables
python suggest_merges.py  # writes CREDITOR_MERGE_SUGGESTIONS
streamlit run review_app.py
python incremental.py     # later: add names that are new in the source
```

Output goes to `DEMO_DB.PUBLIC` unless `SNOWFLAKE_DATABASE` / `SNOWFLAKE_SCHEMA` are set.
The source table needs `COMPANY` and `_FIVETRAN_DELETED` columns; point `CREDITOR_SOURCE_TABLE`
at yours, or create a small demo one with [snowflake/setup_demo.sql](snowflake/setup_demo.sql).
The curated alias list is read from `CREDITOR_ALIASES_CSV` (the sample file by default).
Login uses browser SSO unless `SNOWFLAKE_PASSWORD` is set.

| Table | Contents |
| --- | --- |
| `CREDITORS_NORMALIZED` | Each raw name, its cleaned form and row count |
| `CREDITOR_ALIAS_MAP` | Cleaned name → canonical creditor, with tier and score. This is the table reports join to. |
| `CANONICAL_CREDITORS` | One row per creditor, with total source rows |
| `CREDITOR_REVIEW_QUEUE` | Near matches waiting for a person |

## What I'd improve

- The thresholds were tuned by reviewing samples at each score range, not against a labeled set.
  A few hundred hand-labeled pairs would let me measure precision per tier.
- Matching only uses the name. Adding address or phone would help separate unrelated companies
  with similar names.
- An approved merge moves one name. If that name was the head of a cluster, the other members
  get re-clustered on the next run instead of following it.
