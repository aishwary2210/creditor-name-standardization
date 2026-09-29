"""Small fictional seed list for the public Snowflake scripts.

The working project's curated creditor list is intentionally excluded.
"""

ALIAS_MAP: dict[str, str] = {
    "ASTER BANK": "ASTER BANK",
    "ASTER BK": "ASTER BANK",
    "BLUE HARBOR CREDIT": "BLUE HARBOR CREDIT",
    "BLUE HARBOR CR": "BLUE HARBOR CREDIT",
    "CEDAR FINANCE": "CEDAR FINANCE",
    "CEDAR FIN": "CEDAR FINANCE",
    "ORION FUNDING": "ORION FUNDING",
    "RIVERBEND LENDING": "RIVERBEND LENDING",
}

CANONICAL_NAMES = sorted(set(ALIAS_MAP.values()))


def lookup(normalized_name: str) -> str | None:
    key = normalized_name.strip()
    return ALIAS_MAP.get(key) or ALIAS_MAP.get(key.rstrip("/. "))
