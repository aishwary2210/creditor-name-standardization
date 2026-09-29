"""Connection and table settings for the Snowflake scripts.

Set these environment variables for a Snowflake account you control. No
credentials or company-specific object names are stored in this repository.
"""

import os
import re


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value):
        raise ValueError(f"Invalid Snowflake identifier: {value!r}")
    return value.upper()


TARGET_DATABASE = _identifier(os.getenv("SNOWFLAKE_DATABASE", "DEMO_DB"))
TARGET_SCHEMA_NAME = _identifier(os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"))
TARGET_SCHEMA = f"{TARGET_DATABASE}.{TARGET_SCHEMA_NAME}"

SOURCE_TABLE_NAME = _identifier(os.getenv("CREDITOR_SOURCE_TABLE", "RAW_CREDITORS"))
SOURCE_TABLE = f"{TARGET_SCHEMA}.{SOURCE_TABLE_NAME}"
NORMALIZED_TABLE = f"{TARGET_SCHEMA}.CREDITORS_NORMALIZED"

SNOWFLAKE_CONFIG = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT", ""),
    "user": os.getenv("SNOWFLAKE_USER", ""),
    "role": os.getenv("SNOWFLAKE_ROLE", ""),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", ""),
    "database": TARGET_DATABASE,
    "schema": TARGET_SCHEMA_NAME,
}
if os.getenv("SNOWFLAKE_PASSWORD"):
    SNOWFLAKE_CONFIG["password"] = os.environ["SNOWFLAKE_PASSWORD"]
else:
    SNOWFLAKE_CONFIG["authenticator"] = "externalbrowser"

# Example values for the public project. Tune against reviewed results before
# using a matching threshold on real data.
THRESHOLDS = {
    "auto_accept": 0.96,
    "review_min": 0.91,
    "cluster_merge": 0.92,
}
HIGH_VOLUME_THRESHOLD = 1000
MIN_TOKEN_OVERLAP = 0.5
MAX_LENGTH_RATIO = 2.0
MAX_BLOCK_SIZE = 100

CONFIDENCE_TIERS = {
    "verified": "VERIFIED",
    "seed_fuzzy": "SEED_FUZZY",
    "clustered": "CLUSTERED",
    "standalone": "STANDALONE",
    "low_frequency": "LOW_FREQUENCY",
}
OUTPUT_TABLES = {
    "canonical": f"{TARGET_SCHEMA}.CANONICAL_CREDITORS",
    "alias_map": f"{TARGET_SCHEMA}.CREDITOR_ALIAS_MAP",
    "review_queue": f"{TARGET_SCHEMA}.CREDITOR_REVIEW_QUEUE",
}
PIPELINE_VERSION = "public-example"
