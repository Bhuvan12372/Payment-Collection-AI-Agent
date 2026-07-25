"""
Typed data structures used throughout the agent.

Two categories:
1. `ExtractedFields` — what the extraction layer (LLM + regex) pulls out of
   a raw user message. Always OPTIONAL fields; None means "not present in
   this message". This is intentionally stage-agnostic (see extraction.py)
   so out-of-order information is never dropped.
2. `ConversationState` — everything the agent remembers between turns.
   This is the single source of truth the router (state_machine.py) reads
   and mutates. It is fully serializable (plain types only) so it can be
   persisted/inspected/tested without any LLM or network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# AccountData lives in tools.py (Pydantic model, validated against the live
# lookup-account response). Re-exported here so conversation state can
# reference the same type without a circular import.
from tools import AccountData  # noqa: F401



class Stage(str, Enum):
    GREETING = "GREETING"
    AWAITING_ACCOUNT_ID = "AWAITING_ACCOUNT_ID"
    AWAITING_NAME = "AWAITING_NAME"
    AWAITING_SECONDARY_FACTOR = "AWAITING_SECONDARY_FACTOR"
    AWAITING_AMOUNT = "AWAITING_AMOUNT"
    AWAITING_CARD_DETAILS = "AWAITING_CARD_DETAILS"
    RECAP_DONE = "RECAP_DONE"
    TERMINATED_ACCOUNT_NOT_FOUND = "TERMINATED_ACCOUNT_NOT_FOUND"
    TERMINATED_VERIFICATION_FAILED = "TERMINATED_VERIFICATION_FAILED"
    TERMINATED_PAYMENT_RETRIES_EXCEEDED = "TERMINATED_PAYMENT_RETRIES_EXCEEDED"


@dataclass
class ExtractedFields:
    """Whatever could be pulled out of a single user message. All optional."""
    account_id: Optional[str] = None
    full_name: Optional[str] = None
    dob: Optional[str] = None            # normalized YYYY-MM-DD if found
    aadhaar_last4: Optional[str] = None  # exactly 4 digits
    pincode: Optional[str] = None        # exactly 6 digits (India standard)
    amount: Optional[float] = None
    wants_full_balance: bool = False     # "clear the full amount" type phrasing
    card_number: Optional[str] = None    # digits only, spaces/dashes stripped
    card_expiry_month: Optional[int] = None
    card_expiry_year: Optional[int] = None
    cvv: Optional[str] = None
    cardholder_name: Optional[str] = None


@dataclass
class ConversationState():
    stage: Stage = Stage.GREETING
    retries: int = 0
    # Account identity
    account_id: Optional[str] = None
    full_name: Optional[str] = None
    dob: Optional[str] = None
    aadhaar_last4: Optional[str] = None
    pincode: Optional[str] = None
    balance: float = 0.0

    # Verification progress. name_verified/secondary_verified are tracked
    # separately because a user may supply them across two turns (name
    # first, factor next), but retries are counted as ONE combined counter
    # since "name matches AND secondary factor matches" is a single logical
    # check per the spec — this avoids granting up to 3+3=6 attempts by
    # having two independent limits for what is really one verification step.
    name_input: Optional[str] = None
    name_verified: bool = False
    secondary_verified: bool = False
    verified: bool = False

    # Payment
    amount_to_pay: Optional[float] = None
    amount_paid_so_far: float = 0.0
    card_number: Optional[str] = None
    card_expiry_month: Optional[int] = None
    card_expiry_year: Optional[int] = None
    cvv: Optional[str] = None
    cardholder_name: Optional[str] = None

    # Outcome
    last_transaction_id: Optional[str] = None
    last_error_code: Optional[str] = None

    # Full turn-by-turn transcript (useful for eval/debugging, never sent back
    # to the user verbatim, never includes raw card data beyond this session)
    turns: list = field(default_factory=list)
    close_reason: Optional[str] = None

    def remaining_balance(self) -> Optional[float]:
        return round(self.balance - self.amount_paid_so_far, 2)

    @property
    def closed(self) -> bool:
        return self.close_reason is not None