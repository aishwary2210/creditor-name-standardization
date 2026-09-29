"""Snowflake connection and table helpers.

Settings come from environment variables so nothing account-specific is
stored in the repo. Authentication uses browser SSO unless SNOWFLAKE_PASSWORD
is set.
"""

import os

import pandas as pd
import snowflake.connector
from snowflake.connector.errors import ProgrammingError
from snowflake.connector.pandas_tools import write_pandas

DATABASE = os.getenv("SNOWFLAKE_DATABASE", "DEMO_DB").upper()
SCHEMA = os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC").upper()
SOURCE_TABLE = os.getenv("CREDITOR_SOURCE_TABLE", f"{DATABASE}.{SCHEMA}.RAW_CREDITORS")
ALIASES_CSV = os.getenv("CREDITOR_ALIASES_CSV", "data/sample_aliases.csv")

NORMALIZED = "CREDITORS_NORMALIZED"
CANONICALS = "CANONICAL_CREDITORS"
ALIAS_MAP = "CREDITOR_ALIAS_MAP"
REVIEW_QUEUE = "CREDITOR_REVIEW_QUEUE"
SUGGESTIONS = "CREDITOR_MERGE_SUGGESTIONS"
OVERRIDES = "CREDITOR_MANUAL_OVERRIDES"


def table(name):
    return f"{DATABASE}.{SCHEMA}.{name}"


def connect():
    settings = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "role": os.getenv("SNOWFLAKE_ROLE"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
        "database": DATABASE,
        "schema": SCHEMA,
    }
    if os.getenv("SNOWFLAKE_PASSWORD"):
        settings["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    else:
        settings["authenticator"] = "externalbrowser"
    return snowflake.connector.connect(**settings)


def query(conn, sql, params=None):
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def load_overrides(conn):
    """Approved {normalized name: canonical} pairs from the review app, if any."""
    try:
        return dict(query(conn, f"SELECT ORIGINAL_NAME, CANONICAL_NAME FROM {table(OVERRIDES)}"))
    except ProgrammingError:
        return {}  # table is created the first time someone approves a merge


def write_table(conn, name, rows, columns, replace):
    """Write rows to a table, replacing it or appending to it."""
    if not rows:
        if replace:
            query(conn, f"DROP TABLE IF EXISTS {table(name)}")
        return
    df = pd.DataFrame(rows, columns=columns)
    df.columns = [c.upper() for c in df.columns]
    write_pandas(conn, df, name, database=DATABASE, schema=SCHEMA,
                 auto_create_table=True, overwrite=replace)
