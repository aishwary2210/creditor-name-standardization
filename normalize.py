# Normalization functions for creditor company names
#
# Contains normalize_company() — the core function that cleans raw COMPANY strings.
# Used by main.py (batch) and incremental.py (ongoing).
# Only cares about COMPANY name — no address, phone, or other fields.

import re
import unicodedata
from cleanco import basename as cleanco_basename


# =============================================================================
# LEGAL SUFFIX STRIPPING
# =============================================================================
# cleanco handles most international suffixes, but we have a backup list
# for edge cases it misses (especially "NA" = National Association for banks).

EXTRA_LEGAL_SUFFIXES = [
    r'\bN\.?A\.?\b',   # National Association (banks)
    r'\bFSB\b',        # Federal Savings Bank
    r'\bSSB\b',        # State Savings Bank
]


# =============================================================================
# TOKEN SUBSTITUTION DICTIONARY
# =============================================================================
# Maps common credit bureau abbreviations to their full names.
# Add new patterns here as you discover them in the data.

TOKEN_SUBSTITUTIONS = {
    # Major issuers
    r'\bSYNCB\b': 'SYNCHRONY BANK',
    r'\bSYNC\b': 'SYNCHRONY',
    r'\bCAP1\b': 'CAPITAL ONE',
    r'\bCAP ONE\b': 'CAPITAL ONE',
    r'\bCAPONE\b': 'CAPITAL ONE',
    r'\bWFNNB\b': 'WORLD FINANCIAL NETWORK NATIONAL BANK',
    r'\bCOMENITY\b': 'COMENITY',
    r'\bCBNA\b': 'CITIBANK',
    r'\bJPMCB\b': 'JPMORGAN CHASE BANK',
    r'\bUSAA\b': 'USAA',
    r'\bAMEX\b': 'AMERICAN EXPRESS',

    # Common abbreviations
    r'\bBK\b': 'BANK',
    r'\bNATL\b': 'NATIONAL',
    r"\bNAT\'?L\b": 'NATIONAL',
    r'\bDEPT\b': 'DEPARTMENT',
    r'\bFED\b': 'FEDERAL',
    r'\bSVCS?\b': 'SERVICES',
    r'\bSERV\b': 'SERVICES',
    r'\bFIN\b': 'FINANCIAL',
    r'\bFINL\b': 'FINANCIAL',
    r'\bMGMT\b': 'MANAGEMENT',
    r'\bMTG\b': 'MORTGAGE',
    r'\bINS\b': 'INSURANCE',
    r'\bASSN\b': 'ASSOCIATION',
    r'\bASSOC\b': 'ASSOCIATION',
    r'\bCORP\b': 'CORPORATION',
    r'\bUNIV\b': 'UNIVERSITY',
    r'\bHOSP\b': 'HOSPITAL',
    r'\bMED\b': 'MEDICAL',
    r'\bCTR\b': 'CENTER',
    r'\bGRP\b': 'GROUP',
    r'\bINTL\b': 'INTERNATIONAL',
}


# =============================================================================
# NORMALIZATION FUNCTION
# =============================================================================

def preprocess_company(raw: str) -> str:
    """
    Light preprocessing for alias map lookup — uppercase, strip whitespace,
    bureau prefixes, tribal codes, DBA, but NO token expansion or legal
    suffix stripping. This matches how the seed alias keys are stored.
    """
    if not raw or not isinstance(raw, str):
        return ""
    name = raw.replace('\r', '').replace('\n', ' ').replace('\\r', '').replace('\\n', ' ')
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    name = name.upper().strip()
    name = re.sub(r'\s+', ' ', name)
    # Strip leading 2-digit bureau prefix
    name = re.sub(r'^\d{2}\s+', '', name)
    # Strip trailing tribal codes [IA], [PA], etc.
    name = re.sub(r'\s*\[.*?\]\s*$', '', name)
    # Strip trailing "INSTALLMENT LOAN" / "INSTALLMENT"
    name = re.sub(r'\s+INSTALLMENT\s+LOAN\s*$', '', name)
    name = re.sub(r'\s+INSTALLMENT\s*$', '', name)
    # "X DBA Y" -> keep Y
    name = re.sub(r'^.+\s+DBA\s+', '', name)
    return name.strip()


def normalize_company(raw: str) -> str:
    """
    Full normalization pipeline for a creditor company name.

    Steps (order matters — each feeds the next):
      1. Strip carriage returns and newlines
      2. Strip literal '\\r' strings (stored as backslash+r in some rows)
      3. Preprocess: bureau prefixes, DBA extraction, tribal codes, installment
      4. Convert unicode to ASCII
      5. Uppercase
      6. Strip legal suffixes via cleanco
      7. Strip extra suffixes cleanco misses
      8. Apply token substitution rules
      9. Remove standalone punctuation
      10. Collapse multiple spaces
      11. Strip trailing/leading hyphens and slashes
    """
    if not raw or not isinstance(raw, str):
        return ""

    # 1. Strip actual carriage returns and newlines
    name = raw.replace('\r', '').replace('\n', ' ')

    # 2. Strip literal '\r' stored as two characters (backslash + r)
    name = name.replace('\\r', '').replace('\\n', ' ')

    # 3. Preprocess common source formatting
    name = name.strip()
    name = re.sub(r'\s+', ' ', name)
    # Strip leading 2-digit bureau prefix (e.g. "08 CAPITAL ONE" -> "CAPITAL ONE")
    name = re.sub(r'^\d{2}\s+', '', name)
    # Unescape CSV backslash-escaped commas
    name = name.replace('\\,', ',')
    # Strip trailing tribal/sovereign account-type codes: [IA], [PA], [WI], etc.
    name = re.sub(r'\s*\[.*?\]\s*$', '', name)
    # Strip trailing "Installment Loan" or "Installment"
    name = re.sub(r'\s+[Ii]nstallment\s+[Ll]oan\s*$', '', name)
    name = re.sub(r'\s+[Ii]nstallment\s*$', '', name)
    # "X dba Y" / "X DBA Y" -> use operating name Y
    name = re.sub(r'^.+\s+[Dd][Bb][Aa]\s+', '', name)

    # 4. Convert unicode/accented to ASCII
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')

    # 5. Uppercase
    name = name.upper().strip()

    # 6. Strip legal suffixes using cleanco
    name = cleanco_basename(name)

    # 7. Strip extra suffixes cleanco misses
    for pattern in EXTRA_LEGAL_SUFFIXES:
        name = re.sub(pattern, '', name)

    # 8. Apply token substitution rules
    for pattern, replacement in TOKEN_SUBSTITUTIONS.items():
        name = re.sub(pattern, replacement, name)

    # 9. Remove standalone punctuation (keep letters, numbers, /, -, &)
    name = re.sub(r'[^\w\s/\-&]', '', name)

    # 10. Collapse multiple spaces
    name = re.sub(r'\s+', ' ', name).strip()

    # 11. Strip trailing/leading hyphens and slashes
    name = name.strip('-/ ')

    return name
