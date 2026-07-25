"""
validators.py — Pydantic models used for shape validation of extracted fields.
These raise ValidationError on bad input so the agent can give a clear message
without ever calling the API with garbage.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AccountIdValidator(BaseModel):
    account_id: str

    @field_validator("account_id")
    @classmethod
    def _format(cls, v: str) -> str:
        v = v.strip().upper()
        if not re.fullmatch(r"ACC\d{3,}", v):
            raise ValueError("must look like ACC1001")
        return v


class NameValidator(BaseModel):
    name: str = Field(min_length=2)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())   


class DOBValidator(BaseModel):
    dob: str  # expected YYYY-MM-DD

    @field_validator("dob")
    @classmethod
    def _format_and_range(cls, v: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError("must be YYYY-MM-DD")
        y, m, d = map(int, v.split("-"))
        try:
            parsed = date(y, m, d)
        except ValueError:
            raise ValueError("invalid calendar date")
        if parsed > date.today():
            raise ValueError("date of birth cannot be in the future")
        if y < 1900:
            raise ValueError("year too far in the past")
        return v


class AadhaarValidator(BaseModel):
    aadhaar_last4: str

    @field_validator("aadhaar_last4")
    @classmethod
    def _four_digits(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 4):
            raise ValueError("must be exactly 4 digits")
        return v


class PincodeValidator(BaseModel):
    pincode: str

    @field_validator("pincode")
    @classmethod
    def _six_digits(cls, v: str) -> str:
        if not (v.isdigit() and len(v) == 6):
            raise ValueError("must be exactly 6 digits")
        return v


class AmountValidator(BaseModel):
    amount: Decimal

    @field_validator("amount")
    @classmethod
    def _positive_two_decimals(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("must be > 0")
        if v.as_tuple().exponent < -2:
            raise ValueError("at most 2 decimal places")
        return v


class CardNumberValidator(BaseModel):
    card_number: str

    @field_validator("card_number")
    @classmethod
    def _digits_and_luhn(cls, v: str) -> str:
        if not (v.isdigit() and 12 <= len(v) <= 19):
            raise ValueError("must be 12-19 digits")
        # Optional Luhn check – recommended
        def luhn(n: str) -> bool:
            digits = [int(d) for d in n]
            odd = digits[-1::-2]
            even = [sum(divmod(2 * d, 10)) for d in digits[-2::-2]]
            return (sum(odd) + sum(even)) % 10 == 0
        if not luhn(v):
            raise ValueError("failed Luhn check")
        return v


class CVVValidator(BaseModel):
    cvv: str

    @field_validator("cvv")
    @classmethod
    def _three_or_four(cls, v: str) -> str:
        if not (v.isdigit() and len(v) in (3, 4)):
            raise ValueError("must be 3 or 4 digits")
        return v


class ExpiryValidator(BaseModel):
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2000, le=2100)

    @model_validator(mode="after")
    def _not_expired(self):
        today = date.today()
        # Card is valid through the last day of the expiry month
        if (self.expiry_year, self.expiry_month) < (today.year, today.month):
            raise ValueError("card has expired")
        return self