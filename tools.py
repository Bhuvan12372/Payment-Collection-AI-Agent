"""
tools.py — the ONLY module that talks to the external payment/verification
API. Both functions here are deterministic: they are invoked by the state
machine (never by an LLM deciding to call them), validate their payload
BEFORE sending it, and translate every failure mode into a typed, specific
reason rather than a bare exception.

Design goals (per assignment: "Construct correct, validated payloads before
calling", "Handle all API responses"):
  1. Validate locally first (Pydantic) so obviously-bad payloads never hit
     the network -> categorized as "invalid_payload", not "api_error".
  2. Distinguish failure categories explicitly:
       - invalid_payload   : we caught a bad payload before sending (our bug
                              or bad upstream data, not the user's fault at
                              the network level)
       - network_error     : request never got a response (timeout, DNS,
                              connection refused)
       - not_found         : lookup-account returned 404
       - api_error         : payment API returned a structured error_code
                              (insufficient_balance, invalid_card, etc.)
       - unexpected_response: 200 but the body didn't match the documented
                              shape, or a non-200/404/422 status we don't
                              recognize
  3. Never let a raw exception/stack trace leak into a user-facing message.
"""

from __future__ import annotations

from typing import Optional

import requests
from pydantic import BaseModel, Field, field_validator

import config


# ---------------------------------------------------------------------------
# Pydantic schemas — the account record and the payment request/response.
# Using real Pydantic (not dataclasses) here specifically because this is
# data crossing a network boundary: we want type coercion + validation
# errors raised automatically if the API ever returns something malformed.
# ---------------------------------------------------------------------------

class AccountData(BaseModel):
    """Validated shape of a successful lookup-account response. This is the
    single stored source of truth an agent instance verifies user input
    against for the rest of the conversation."""
    account_id: str
    full_name: str
    dob: str
    aadhaar_last4: str
    pincode: str
    balance: float

    @field_validator("dob")
    @classmethod
    def _dob_format(cls, v: str) -> str:
        import re
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError(f"dob '{v}' is not in YYYY-MM-DD format")
        return v

    @field_validator("aadhaar_last4")
    @classmethod
    def _aadhaar_format(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError(f"aadhaar_last4 '{v}' must be exactly 4 digits")
        return v

    @field_validator("pincode")
    @classmethod
    def _pincode_format(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 6):
            raise ValueError(f"pincode '{v}' must be exactly 6 digits")
        return v


class CardDetails(BaseModel):
    cardholder_name: str = Field(min_length=1)
    card_number: str
    cvv: str
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2000, le=2100)

    @field_validator("card_number")
    @classmethod
    def _card_number_format(cls, v: str) -> str:
        if not (v.isdigit() and 12 <= len(v) <= 19):
            raise ValueError("card_number must be 12-19 digits")
        return v

    @field_validator("cvv")
    @classmethod
    def _cvv_format(cls, v: str) -> str:
        if not (v.isdigit() and len(v) in (3, 4)):
            raise ValueError("cvv must be 3 or 4 digits")
        return v


class PaymentRequest(BaseModel):
    account_id: str
    amount: float = Field(gt=0)
    card: CardDetails

    @field_validator("amount")
    @classmethod
    def _two_decimal_places(cls, v: float) -> float:
        if round(v, 2) != v:
            raise ValueError("amount must have at most 2 decimal places")
        return v


class PaymentSuccess(BaseModel):
    success: bool
    transaction_id: str


class PaymentFailure(BaseModel):
    success: bool
    error_code: str


# ---------------------------------------------------------------------------
# Typed exceptions — every failure path raises one of these, each carrying
# a `reason` category and a `detail` string safe to log (never raw card
# data) plus, for api_error, the specific error_code from the API.
# ---------------------------------------------------------------------------

class ToolError(Exception):
    def __init__(self, reason: str, detail: str, error_code: Optional[str] = None):
        self.reason = reason          # invalid_payload | network_error |
                                       # not_found | api_error | unexpected_response
        self.detail = detail          # human-readable, safe to show/log
        self.error_code = error_code  # raw API error_code, if any
        super().__init__(f"[{reason}] {detail}")


# ---------------------------------------------------------------------------
# lookup_account
# ---------------------------------------------------------------------------

def lookup_account(account_id: str) -> AccountData:
    """Calls POST /api/lookup-account. Returns a validated AccountData on
    success. Raises ToolError with a specific `reason` on any failure."""
    if not account_id or not account_id.strip():
        raise ToolError("invalid_payload", "account_id is empty.")

    payload = {"account_id": account_id}

    try:
        resp = requests.post(
            config.LOOKUP_ACCOUNT_ENDPOINT,
            json=payload,
            timeout=config.API_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        raise ToolError("network_error", "Lookup request timed out.")
    except requests.exceptions.ConnectionError as e:
        raise ToolError("network_error", f"Could not reach the account lookup service: {e}")
    except requests.exceptions.RequestException as e:
        raise ToolError("network_error", f"Unexpected network failure during lookup: {e}")

    if resp.status_code == 200:
        try:
            body = resp.json()
        except ValueError:
            raise ToolError("unexpected_response", "Lookup API returned non-JSON on 200 OK.")
        try:
            return AccountData(**body)
        except Exception as e:
            # 200 OK but the body doesn't match the documented schema —
            # this is an upstream data problem, not something the user can fix.
            raise ToolError("unexpected_response", f"Lookup response failed validation: {e}")

    if resp.status_code == 404:
        raise ToolError("not_found", "No account found with the provided account_id.")

    # Any other status code we don't have documented handling for.
    raise ToolError(
        "unexpected_response",
        f"Lookup API returned unexpected status {resp.status_code}: {resp.text[:200]}",
    )


# ---------------------------------------------------------------------------
# process_payment
# ---------------------------------------------------------------------------

def process_payment(
    account_id: str,
    amount: float,
    cardholder_name: str,
    card_number: str,
    cvv: str,
    expiry_month: int,
    expiry_year: int,
) -> PaymentSuccess:
    """Calls POST /api/process-payment. Validates the payload locally first
    (so malformed input never even reaches the network), then interprets
    the response. Raises ToolError with `reason="invalid_payload"` for
    local validation failures, `reason="api_error"` (with `error_code` set
    to the API's documented code) for a 422, and `network_error` /
    `unexpected_response` for everything else."""

    # 1) Local validation BEFORE calling the API.
    try:
        request_model = PaymentRequest(
            account_id=account_id,
            amount=amount,
            card=CardDetails(
                cardholder_name=cardholder_name,
                card_number=card_number,
                cvv=cvv,
                expiry_month=expiry_month,
                expiry_year=expiry_year,
            ),
        )
    except Exception as e:
        raise ToolError("invalid_payload", f"Payment payload failed local validation: {e}")

    payload = {
        "account_id": request_model.account_id,
        "amount": request_model.amount,
        "payment_method": {
            "type": "card",
            "card": {
                "cardholder_name": request_model.card.cardholder_name,
                "card_number": request_model.card.card_number,
                "cvv": request_model.card.cvv,
                "expiry_month": request_model.card.expiry_month,
                "expiry_year": request_model.card.expiry_year,
            },
        },
    }

    # 2) Network call.
    try:
        resp = requests.post(
            config.PROCESS_PAYMENT_ENDPOINT,
            json=payload,
            timeout=config.API_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        raise ToolError("network_error", "Payment request timed out.")
    except requests.exceptions.ConnectionError as e:
        raise ToolError("network_error", f"Could not reach the payment service: {e}")
    except requests.exceptions.RequestException as e:
        raise ToolError("network_error", f"Unexpected network failure during payment: {e}")

    # 3) Interpret response.
    if resp.status_code == 200:
        try:
            body = resp.json()
            return PaymentSuccess(**body)
        except Exception as e:
            raise ToolError("unexpected_response", f"Payment success response failed validation: {e}")

    if resp.status_code == 422:
        try:
            body = resp.json()
            failure = PaymentFailure(**body)
        except Exception:
            raise ToolError("unexpected_response", "Payment failed but error body was malformed.")
        raise ToolError("api_error", f"Payment declined: {failure.error_code}", error_code=failure.error_code)

    raise ToolError(
        "unexpected_response",
        f"Payment API returned unexpected status {resp.status_code}: {resp.text[:200]}",
    )