"""
extraction.py — turns one user message into candidate field values.

Two extractors, called once each per turn (not per field):
  1. extract_deterministic(text)  -> regex/date-parser only, no network,
     no LLM, no API key required. Handles: account_id, aadhaar_last4,
     pincode, dob (numeric/ISO formats), card_number, expiry.
     These are fixed-shape strings; an LLM adds latency and risk here with
     zero accuracy benefit.
  2. llm_client.default_client.extract_all(text) -> ONE Groq call covering
     every ambiguous free-form field (full_name, amount, wants_full_balance,
     cvv, cardholder_name override, dob fallback for phrasing regex can't
     parse). See llm_client.py for why this is one call/one schema rather
     than one call per field type.

extract(text, currently_awaiting) merges both, with deterministic results
taking priority (never overwritten by the LLM pass) since they're the more
trustworthy signal for their fields.

This module NEVER decides correctness against the account — that is
verification.py's job, deliberately kept separate so the strict-match rule
can never be quietly bypassed by an extraction heuristic.
"""

from __future__ import annotations

import re
from typing import Optional

from dateutil import parser as dateparser

from llm_client import default_client
from state import ExtractedFields

_ACCOUNT_ID_RE = re.compile(r"\bacc\s*[-_ ]?\s*(\d{3,})\b", re.IGNORECASE)


def _extract_account_id(text: str) -> Optional[str]:
    m = _ACCOUNT_ID_RE.search(text)
    return f"ACC{m.group(1)}" if m else None


def _extract_labeled_digits(text: str, label_pattern: str, n: int) -> Optional[str]:
    """`<label> ... <exactly n digits>` within a short window, tolerant of
    spaces/dashes between digits. Boundary checks prevent matching a slice
    of a longer number."""
    pattern = rf"(?:{label_pattern}).{{0,25}}?(?<!\d)((?:\d[\s-]*){{{n}}})(?!\d)"
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    digits = re.sub(r"[\s-]", "", m.group(1))
    return digits if len(digits) == n else None


def _extract_dob_numeric(text: str) -> Optional[str]:
    """Handles ISO and DD/MM/YYYY-style numeric dates deterministically."""
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _validate_and_format(y, mo, d)

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 1900 if y > 30 else 2000
        return _validate_and_format(y, mo, d)

    return None


_DATE_SIGNAL_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|born|dob|birth)\w*\b",
    re.IGNORECASE,
)


def _extract_dob_natural_language(text: str) -> Optional[str]:
    """Deterministic (not LLM) natural-language date parsing via dateutil,
    for phrasing like '14th May 1990' or 'I was born on 29th Feb 1988'.
    This is fixed-shape enough (a month name + day + year, in some order)
    that a real date parser handles it reliably without needing the LLM —
    gated on an explicit date signal so dateutil's fuzzy mode doesn't
    hallucinate a date out of unrelated digits elsewhere in the sentence."""
    if not _DATE_SIGNAL_RE.search(text):
        return None
    try:
        dt = dateparser.parse(text, fuzzy=True, dayfirst=True, default=None)
    except (ValueError, OverflowError, TypeError):
        return None
    if dt is None:
        return None
    year = dt.year
    if year < 100:
        year += 1900 if year > 30 else 2000
    return _validate_and_format(year, dt.month, dt.day)


def _validate_and_format(y: int, mo: int, d: int) -> Optional[str]:
    try:
        dt = dateparser.parse(f"{y:04d}-{mo:02d}-{d:02d}", yearfirst=True)
        # dateutil silently rolls invalid dates (e.g. Feb 30) forward unless
        # we double check the components round-trip exactly.
        if (dt.year, dt.month, dt.day) != (y, mo, d):
            return None
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except (ValueError, OverflowError):
        return None


def _extract_card_number(text: str) -> Optional[str]:
    # Strip expiry (MM/YYYY or 'December 2027') and any CVV mention FIRST —
    # otherwise a compact payload like "4532015112830366 12/28 123" could
    # merge the card number with the CVV token.
    without_expiry = re.sub(r"\b\d{1,2}\s*/\s*\d{2,4}\b", " ", text)
    months = "january|february|march|april|may|june|july|august|september|october|november|december"
    without_expiry = re.sub(rf"\b({months})[a-z]*\.?\s+\d{{4}}\b", " ", without_expiry, flags=re.IGNORECASE)
    without_cvv = re.sub(r"cvv\D{0,10}\d{3,4}\b", " ", without_expiry, flags=re.IGNORECASE)

    # Preserve separators between card-number groups so we do not accidentally
    # merge the card number with a following CVV token. We still support
    # compact 16-digit input and grouped input like "4532 0151 1283 0366".
    cleaned = re.sub(r"(?<=\d)[\s-]+(?=\d)", " ", without_cvv)

    # First prefer a single long numeric token (e.g. "4532015112830366").
    for token in re.findall(r"\d+", cleaned):
        if 12 <= len(token) <= 19:
            return token

    # Fallback: join consecutive 4-digit groups if the user supplied a spaced form.
    groups = re.findall(r"\b\d{4}\b", cleaned)
    if len(groups) >= 3:
        joined = "".join(groups[:4])
        if 12 <= len(joined) <= 19:
            return joined

    return None


def _extract_expiry(text: str) -> tuple[Optional[int], Optional[int]]:
    m = re.search(r"\b(\d{1,2})\s*/\s*(\d{2,4})\b", text)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if year < 100:
            year += 2000
        return (month, year) if 1 <= month <= 12 else (None, None)

    months = ["january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december"]
    m = re.search(
        rf"\b({'|'.join(months)})\s+(\d{{4}})\b", text, re.IGNORECASE,
    )
    if m:
        return months.index(m.group(1).lower()) + 1, int(m.group(2))
    return None, None


_FULL_BALANCE_PHRASES = re.compile(
    r"\b(full amount|entire amount|whole amount|clear the full|"
    r"pay it all|full balance|everything|all of it)\b",
    re.IGNORECASE,
)


def _extract_amount_numeric(text: str) -> Optional[float]:
    """Plain numeric amounts (with optional currency symbol/word) — fixed
    enough shape that regex is exactly as good as an LLM here."""
    m = re.search(
        r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:rs\.?|rupees|inr)?",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_cvv_numeric(text: str) -> Optional[str]:
    m = re.search(r"cvv\D{0,10}(\d{3,4})\b", text, re.IGNORECASE)
    return m.group(1) if m else None


def extract_deterministic(text: str) -> ExtractedFields:
    fields = ExtractedFields()
    fields.account_id = _extract_account_id(text)
    fields.aadhaar_last4 = _extract_labeled_digits(text, "aadhaar", 4)
    fields.pincode = _extract_labeled_digits(text, r"pin\s*-?\s*code|pincode", 6)
    fields.dob = _extract_dob_numeric(text) or _extract_dob_natural_language(text)
    fields.card_number = _extract_card_number(text)
    fields.card_expiry_month, fields.card_expiry_year = _extract_expiry(text)
    fields.cvv = _extract_cvv_numeric(text)

    # Amount is the least distinctively-shaped field (just digits), so it's
    # the one most at risk of accidentally matching a card number, account
    # id, or CVV in the same message. Strip whatever's already been
    # confidently identified before searching for an amount.
    remainder = text
    if fields.card_number:
        remainder = re.sub(re.escape(fields.card_number), "", remainder)
        remainder = re.sub(r"(?<=\d)[\s-]+(?=\d)", "", remainder)  # re-collapse leftovers
    if fields.account_id:
        remainder = _ACCOUNT_ID_RE.sub("", remainder)
    remainder = re.sub(r"cvv\D{0,10}\d{3,4}", "", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"\b\d{1,2}\s*/\s*\d{2,4}\b", "", remainder)  # expiry

    if _FULL_BALANCE_PHRASES.search(remainder):
        fields.wants_full_balance = True
    else:
        fields.amount = _extract_amount_numeric(remainder)

    return fields


def extract(text: str, currently_awaiting: str = "unknown") -> ExtractedFields:
    """One regex pass + one LLM call (never more), merged with deterministic
    fields taking priority."""
    if currently_awaiting == "AWAITING_NAME":
        fields = ExtractedFields()
        name = default_client.extract_name(text)
        if name:
            fields.full_name = name
        return fields
    else:
        fields = extract_deterministic(text)

    # Bare digit-group fallback: only applied when the agent specifically
    # asked for a secondary factor and no labeled aadhaar/pincode keyword
    # was found. This is stage-dependent (a bare "2468" only means Aadhaar
    # in the context of "what's your Aadhaar/pincode/DOB?"), so it lives
    # here rather than in the label-based extractor, and applies whether
    # or not an LLM is configured.
    if currently_awaiting == "AWAITING_SECONDARY_FACTOR" and not any(
        [fields.aadhaar_last4, fields.pincode, fields.dob]
    ):
        stripped = text.strip()
        if re.fullmatch(r"\d{4}", stripped):
            fields.aadhaar_last4 = stripped
        elif re.fullmatch(r"\d{6}", stripped):
            fields.pincode = stripped
            
    if currently_awaiting == "AWAITING_CARD_DETAILS" and not fields.cvv:
        stripped = text.strip()
        if re.fullmatch(r"\d{3,4}", stripped):
            fields.cvv = stripped

    llm_out = default_client.extract_all(text, currently_awaiting)

    if not fields.full_name and llm_out.get("full_name"):
        fields.full_name = llm_out["full_name"].strip()

    if not fields.dob and llm_out.get("dob"):
        fields.dob = llm_out["dob"]

    if llm_out.get("wants_full_balance"):
        fields.wants_full_balance = True
    elif fields.amount is None and llm_out.get("amount") is not None:
        fields.amount = llm_out["amount"]

    if llm_out.get("cvv"):
        cvv = re.sub(r"\D", "", str(llm_out["cvv"]))
        if len(cvv) in (3, 4):
            fields.cvv = cvv

    if llm_out.get("cardholder_name"):
        fields.cardholder_name = llm_out["cardholder_name"].strip()

    if not fields.card_number and llm_out.get("card_number"):
        cleaned = re.sub(r"\D", "", str(llm_out["card_number"]))
        if 12 <= len(cleaned) <= 19:
            fields.card_number = cleaned

    if (fields.card_expiry_month is None or fields.card_expiry_year is None) and llm_out.get("card_expiry"):
        expiry = str(llm_out["card_expiry"])
        m = re.search(r"(\d{1,2})\D+(\d{2,4})", expiry)
        if m:
            month, year = int(m.group(1)), int(m.group(2))
            if year < 100:
                year += 2000
            fields.card_expiry_month = month
            fields.card_expiry_year = year

    return fields