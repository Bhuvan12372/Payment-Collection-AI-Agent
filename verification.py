"""
Verification logic — deliberately the simplest, most boring module in the
codebase. That is by design: this is the safety-critical piece the spec
requires to be "strict — no fuzzy matching, no case-insensitive workarounds".

Every function here is a pure function over plain strings/values. No LLM
calls, no network calls. This module is 100% deterministic and should be
exhaustively unit tested (see eval/test_verification.py).
"""

from __future__ import annotations

from state import AccountData


def names_match(input_name: str, account_full_name: str) -> bool:
    """Exact match only. No case folding, no whitespace normalization beyond
    trimming leading/trailing whitespace (trimming stray spaces from typing
    is not 'fuzzy matching' — comparing 'Nithin Jain ' vs 'Nithin Jain' as a
    mismatch would be a UX bug, not a security feature)."""
    return input_name.strip() == account_full_name.strip()


def dob_matches(input_dob: str, account_dob: str) -> bool:
    """Exact match on normalized YYYY-MM-DD strings."""
    return input_dob == account_dob


def aadhaar_matches(input_last4: str, account_last4: str) -> bool:
    return input_last4 == account_last4


def pincode_matches(input_pincode: str, account_pincode: str) -> bool:
    return input_pincode == account_pincode


def check_secondary_factor(
    account: AccountData,
    dob: str | None,
    aadhaar_last4: str | None,
    pincode: str | None,
) -> bool:
    """True if ANY provided secondary factor exactly matches. A user only
    needs to provide one; if multiple are provided, matching any one is
    sufficient (spec: 'at least one of the following also matches')."""
    if dob and dob_matches(dob, account.dob):
        return True
    if aadhaar_last4 and aadhaar_matches(aadhaar_last4, account.aadhaar_last4):
        return True
    if pincode and pincode_matches(pincode, account.pincode):
        return True
    return False


def validate_amount(amount: float, balance_remaining: float) -> tuple[bool, str | None]:
    """Client-side pre-validation before hitting the API (spec: 'Validate
    all inputs before calling any API'). Returns (is_valid, error_message).
    This mirrors the API's own invalid_amount rule but lets us give a
    friendlier message before spending an API call."""
    if amount <= 0:
        return False, "The payment amount must be greater than zero."
    # more than 2 decimal places
    if round(amount, 2) != amount:
        return False, "The payment amount can have at most 2 decimal places."
    if amount > balance_remaining:
        return False, (
            f"That amount exceeds your outstanding balance of "
            f"₹{balance_remaining:.2f}."
        )
    return True, None