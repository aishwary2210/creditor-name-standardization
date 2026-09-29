"""Cleaning rules for raw creditor names.

The rules come from patterns seen in the source data: two-digit bureau
prefixes, "X DBA Y" names, bracketed account-type codes, legal suffixes and
bureau abbreviations. Add a substitution when a new abbreviation shows up.
"""

import re
import unicodedata

from cleanco import basename

# cleanco misses a few bank-specific suffixes.
EXTRA_LEGAL_SUFFIXES = [r"\bN\.?A\.?\b", r"\bFSB\b", r"\bSSB\b"]

TOKEN_SUBSTITUTIONS = {
    # Issuer abbreviations common in bureau data
    r"\bSYNCB\b": "SYNCHRONY BANK",
    r"\bSYNC\b": "SYNCHRONY",
    r"\bCAP1\b": "CAPITAL ONE",
    r"\bCAP ONE\b": "CAPITAL ONE",
    r"\bCAPONE\b": "CAPITAL ONE",
    r"\bWFNNB\b": "WORLD FINANCIAL NETWORK NATIONAL BANK",
    r"\bCBNA\b": "CITIBANK",
    r"\bJPMCB\b": "JPMORGAN CHASE BANK",
    r"\bAMEX\b": "AMERICAN EXPRESS",
    # General abbreviations
    r"\bBK\b": "BANK",
    r"\bNATL\b": "NATIONAL",
    r"\bNAT'?L\b": "NATIONAL",
    r"\bDEPT\b": "DEPARTMENT",
    r"\bFED\b": "FEDERAL",
    r"\bSVCS?\b": "SERVICES",
    r"\bSERV\b": "SERVICES",
    r"\bFINL?\b": "FINANCIAL",
    r"\bMGMT\b": "MANAGEMENT",
    r"\bMTG\b": "MORTGAGE",
    r"\bINS\b": "INSURANCE",
    r"\bASSN\b": "ASSOCIATION",
    r"\bASSOC\b": "ASSOCIATION",
    r"\bCORP\b": "CORPORATION",
    r"\bUNIV\b": "UNIVERSITY",
    r"\bHOSP\b": "HOSPITAL",
    r"\bMED\b": "MEDICAL",
    r"\bCTR\b": "CENTER",
    r"\bGRP\b": "GROUP",
    r"\bINTL\b": "INTERNATIONAL",
}


def normalize_company(raw):
    """Return a cleaned, uppercase version of a raw creditor name."""
    if not isinstance(raw, str):
        return ""

    # Some rows contain real line breaks, others the literal characters "\r".
    name = raw.replace("\r", "").replace("\n", " ")
    name = name.replace("\\r", "").replace("\\n", " ").replace("\\,", ",")
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"\s+", " ", name).strip().upper()

    name = re.sub(r"^\d{2} ", "", name)                  # "08 CAPITAL ONE"
    name = re.sub(r"\s*\[.*?\]$", "", name)              # "... [IA]"
    name = re.sub(r" INSTALLMENT( LOAN)?$", "", name)
    name = re.sub(r"^.+ DBA ", "", name)                 # keep the operating name

    name = basename(name)
    for pattern in EXTRA_LEGAL_SUFFIXES:
        name = re.sub(pattern, "", name)
    for pattern, replacement in TOKEN_SUBSTITUTIONS.items():
        name = re.sub(pattern, replacement, name)

    name = re.sub(r"[^\w\s/&-]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip("-/ ")
