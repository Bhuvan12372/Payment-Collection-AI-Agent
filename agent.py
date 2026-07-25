"""
Agent: the required public interface.

    agent = Agent()
    agent.next("Hi") -> {"message": "..."}

Design in one paragraph: a single enum (Stage) tracks where we are in the
conversation. next() is one big dispatch on self.state.stage -- no LangGraph,
no external DB, no threads. All extraction is delegated to extraction.py
(regex for fixed-shape fields, LLM+reflection for name/amount/dob-fallback),
all shape validation to validators.py (Pydantic), all API calls to tools.py,
and the actual verification decision is a plain deterministic equality check
here in the agent -- never an LLM judgment call. Every handler is wrapped by
next()'s outer try/except so a bug in one turn closes the session cleanly
instead of raising to the caller or looping forever.
"""

from decimal import Decimal, InvalidOperation
from pydantic import ValidationError
import json
import logging
import sys
from typing import Optional

from state import Stage, ConversationState
from llm_client import LLMClient
import extraction
import tools
import error_messages
from tools import ToolError
from validators import (
    AccountIdValidator, NameValidator, DOBValidator, AadhaarValidator,
    PincodeValidator, AmountValidator, CardNumberValidator, CVVValidator,
    ExpiryValidator,
)

class Agent:
    def __init__(self):
        self.state = ConversationState(stage=Stage.GREETING)
        self.llm = LLMClient()

    def next(self, user_input: str) -> dict:
        if getattr(self.state, "close_reason", None):
            return {"message": self.state.close_reason or "This conversation has ended."}

        try:
            handler = {
                Stage.GREETING: self._handle_greeting,
                Stage.AWAITING_ACCOUNT_ID: self._handle_account_id,
                Stage.AWAITING_NAME: self._handle_name,
                Stage.AWAITING_SECONDARY_FACTOR: self._handle_secondary_factor,
                Stage.AWAITING_AMOUNT: self._handle_amount,
                Stage.AWAITING_CARD_DETAILS: self._handle_card_details,
            }[self.state.stage]
            message = handler(user_input or "")
        except Exception:
            self.state.close_reason = (
                "Something unexpected went wrong on our end and I can't continue "
                "this session. Please start a new conversation, or contact support "
                "if the issue persists."
            )
            message = self.state.close_reason

        return {"message": message}

    def _change_step(self, stage: Stage):
        if self.state.stage != stage:
            self.state.stage = stage
            self.state.retries = 0

    def _handle_greeting(self, _user_input: str) -> str:
        self._change_step(Stage.AWAITING_ACCOUNT_ID)
        return "Hello! Please share your account ID to get started."

    def _handle_account_id(self, user_input: str) -> str:
        fields = extraction.extract(user_input, "AWAITING_ACCOUNT_ID")
        candidate = fields.account_id
        if candidate is None:
            return self._retry_or_close(
                "I couldn't find an account ID in that. Could you share it again? "
                "It should look like ACC1001."
            )

        try:
            candidate = AccountIdValidator(account_id=candidate).account_id
        except ValidationError:
            return self._retry_or_close(
                f"'{candidate}' doesn't look like a valid account ID format "
                "(expected something like ACC1001). Could you resend it?"
            )

        try:
            data = tools.lookup_account(candidate)
        except ToolError as err:
            msg, retryable = error_messages.message_for_error(err)
            if not retryable:
                self._close(msg)
                return self.state.close_reason
            return self._retry_or_close(msg)

        self.state.account_id = candidate
        self.state.full_name = getattr(data, 'full_name', '') or data.get('full_name', '') if isinstance(data, dict) else data.full_name
        self.state.dob = getattr(data, 'dob', '') or data.get('dob', '') if isinstance(data, dict) else data.dob
        self.state.aadhaar_last4 = getattr(data, 'aadhaar_last4', '') or data.get('aadhaar_last4', '') if isinstance(data, dict) else data.aadhaar_last4
        self.state.pincode = getattr(data, 'pincode', '') or data.get('pincode', '') if isinstance(data, dict) else data.pincode
        self.state.balance = getattr(data, 'balance', 0.0) or data.get('balance', 0.0) if isinstance(data, dict) else data.balance
        self._change_step(Stage.AWAITING_NAME)
        
        # In case they volunteered their name in the very first message along with account ID
        fields = extraction.extract(user_input, "AWAITING_NAME")
        if fields.full_name:
            return self._handle_name(user_input)
            
        return "To verify your identity, could you please share your full name?"

    def _check_secondary_factor(self, fields) -> Optional[str]:
        if fields.dob and fields.dob == self.state.dob:
            return f"Date of birth ({fields.dob})"
        if fields.aadhaar_last4 and fields.aadhaar_last4 == self.state.aadhaar_last4:
            return f"Aadhaar last 4 digits ({fields.aadhaar_last4})"
        if fields.pincode and fields.pincode == self.state.pincode:
            return f"Pincode ({fields.pincode})"
        return None

    def _handle_name(self, user_input: str) -> str:
        fields = extraction.extract(user_input, "AWAITING_NAME")
        
        if fields.full_name:
            try:
                self.state.name_input = NameValidator(name=fields.full_name).name
            except ValidationError:
                pass

        if self.state.name_input and self.state.name_input.lower() == str(self.state.full_name).lower():
            self.state.name_verified = True
            self._change_step(Stage.AWAITING_SECONDARY_FACTOR)
            
            matched_factor = self._check_secondary_factor(fields)
            if matched_factor:
                self.state.secondary_verified = True
                self.state.verified = True
                
                if self.state.balance <= 0:
                    self._close(
                        f"Full name and {matched_factor} verified. Identity verified. "
                        f"Your outstanding balance is ₹0.00. You have nothing due today! Have a great day!",
                        stage=Stage.RECAP_DONE
                    )
                    return self.state.close_reason

                self._change_step(Stage.AWAITING_AMOUNT)
                return (
                    f"Full name and {matched_factor} verified. Identity verified. Your outstanding balance is "
                    f"₹{self.state.balance:.2f}. How much would you like to pay today?"
                )
            return "Thanks -- could you also share one of: your date of birth, Aadhaar last 4 digits, or pincode?"

        def _on_fail():
            if self.state.name_input:
                return f"I understood your name as '{self.state.name_input}', but that doesn't match our records. Could you confirm your full name?"
            return "The name provided doesn't match our records. Could you confirm your full name?"
        self.state.name_input = None
        return self._retry_or_close(None, on_fail=_on_fail)

    def _handle_secondary_factor(self, user_input: str) -> str:
        fields = extraction.extract(user_input, "AWAITING_SECONDARY_FACTOR")
        
        matched_factor = self._check_secondary_factor(fields)
        if matched_factor:
            self.state.secondary_verified = True
            self.state.verified = True
            
            if self.state.balance <= 0:
                self._close(
                    f"{matched_factor} verified. Identity verified. "
                    f"Your outstanding balance is ₹0.00. You have nothing due today! Have a great day!",
                    stage=Stage.RECAP_DONE
                )
                return self.state.close_reason

            self._change_step(Stage.AWAITING_AMOUNT)
            return (
                f"{matched_factor} verified. Identity verified. Your outstanding balance is "
                f"₹{self.state.balance:.2f}. How much would you like to pay today?"
            )
            
        def _on_fail():
            return "That doesn't match our records. Could you provide your date of birth, Aadhaar last 4 digits, or pincode?"
        return self._retry_or_close(None, on_fail=_on_fail)

    def _handle_amount(self, user_input: str) -> str:
        fields = extraction.extract(user_input, "AWAITING_AMOUNT")
        
        amount = None
        if fields.wants_full_balance:
            amount = self.state.balance
        elif fields.amount is not None:
            amount = fields.amount
            
        if amount is None:
            return self._retry_or_close(
                "I didn't catch a valid payment amount. You can say something "
                "like '₹500' or 'the full amount'."
            )

        try:
            validated = AmountValidator(amount=Decimal(str(amount))).amount
        except (ValidationError, InvalidOperation):
            return self._retry_or_close(
                f"'{amount}' doesn't look like a valid payment amount (it must be "
                "positive, with at most 2 decimal places). How much would you like to pay?"
            )

        if validated > Decimal(str(self.state.balance)):
            return self._retry_or_close(
                f"That's more than your outstanding balance of ₹{self.state.balance:.2f}. "
                "How much would you like to pay?"
            )

        self.state.amount_to_pay = float(validated)
        self._change_step(Stage.AWAITING_CARD_DETAILS)
        return (
            "Great -- now could you share your card number, expiry (month and "
            "year), and CVV? I'll use your verified name as the cardholder name "
            "unless you tell me otherwise."
        )

    def _handle_card_details(self, user_input: str) -> str:
        fields = extraction.extract(user_input, "AWAITING_CARD_DETAILS")
        invalid_notes = []

        if fields.card_number:
            try:
                self.state.card_number = CardNumberValidator(card_number=fields.card_number).card_number
            except ValidationError:
                invalid_notes.append("The card number you sent doesn't look valid -- could you resend it?")

        if fields.card_expiry_month and fields.card_expiry_year:
            try:
                validated = ExpiryValidator(expiry_month=fields.card_expiry_month, expiry_year=fields.card_expiry_year)
                self.state.card_expiry_month = validated.expiry_month
                self.state.card_expiry_year = validated.expiry_year
            except ValidationError:
                invalid_notes.append("The expiry doesn't look valid or the card appears expired -- could you resend it?")

        if fields.cvv:
            try:
                self.state.cvv = CVVValidator(cvv=fields.cvv).cvv
            except ValidationError:
                invalid_notes.append("The CVV should be 3 digits (4 for Amex) -- could you resend it?")

        if fields.cardholder_name:
            try:
                self.state.cardholder_name = NameValidator(name=fields.cardholder_name).name
            except ValidationError:
                pass  # keep default

        if invalid_notes:
            return " ".join(invalid_notes)

        missing = []
        if not self.state.card_number:
            missing.append("card number")
            
        if self.state.card_expiry_month is None or self.state.card_expiry_year is None:
            missing.append("expiry (month/year)")
            
        if not self.state.cvv:
            missing.append("CVV")
            
        if missing:
            return f"I still need your {', '.join(missing)}."

        if not self.state.cardholder_name:
            self.state.cardholder_name = self.state.name_input

        return self._process_payment()

    def _process_payment(self) -> str:
        try:
            result = tools.process_payment(
                account_id=self.state.account_id,
                amount=round(self.state.amount_to_pay, 2),
                cardholder_name=self.state.cardholder_name,
                card_number=self.state.card_number,
                cvv=self.state.cvv,
                expiry_month=self.state.card_expiry_month,
                expiry_year=self.state.card_expiry_year,
            )
        except ToolError as err:
            return self._handle_payment_error(err)

        self.state.card_number = None
        self.state.cvv = None

        if getattr(result, "success", False):
            self.state.last_transaction_id = getattr(result, "transaction_id", "UNKNOWN")
            self._close(
                f"Payment of ₹{self.state.amount_to_pay:.2f} was "
                f"successful. Transaction ID: {self.state.last_transaction_id}. Current Balance amount is : ₹{self.state.balance - self.state.amount_to_pay:.2f}. "
                f"Thank you, {self.state.name_input} -- have a great day!",
                stage=Stage.RECAP_DONE
            )
            return self.state.close_reason

        return self._handle_payment_error(ToolError("unexpected_response", "Payment failed without throwing."))

    def _handle_payment_error(self, err: ToolError) -> str:
        msg, retryable = error_messages.message_for_error(err)

        if not retryable:
            self.state.card_number = None
            self.state.cvv = None
            self._close(msg)
            return self.state.close_reason

        def _on_fail():
            self.state.card_expiry_month = None
            self.state.card_expiry_year = None
            error_code = getattr(err, "error_code", None)
            
            if error_code in ("invalid_amount", "insufficient_balance"):
                self.state.amount_to_pay = None
                self._change_step(Stage.AWAITING_AMOUNT)
                return f"Payment didn't go through: {msg} Your outstanding balance is ₹{self.state.balance:.2f}. How much would you like to pay?"
            else:
                self._change_step(Stage.AWAITING_CARD_DETAILS)
                return f"Payment didn't go through: {msg}"

        return self._retry_or_close(None, on_fail=_on_fail)

    def _retry_or_close(self, message: str = None, on_fail=None) -> str:
        self.state.retries += 1
        if self.state.retries >= 3:
            # Set appropriate terminal stage based on current state
            if self.state.stage in (Stage.AWAITING_NAME, Stage.AWAITING_SECONDARY_FACTOR):
                terminal_stage = Stage.TERMINATED_VERIFICATION_FAILED
            else:
                terminal_stage = Stage.TERMINATED_PAYMENT_RETRIES_EXCEEDED

            self._close(
                "I wasn't able to get valid, matching information after several "
                "attempts, so I'll end this session here for security. Please "
                "start a new session or contact support if you need help.",
                stage=terminal_stage
            )
            return self.state.close_reason
        if on_fail is not None:
            return on_fail()
        return message

    def _close(self, message: str, stage: Stage = None):
        self.state.close_reason = message
        if stage:
            self.state.stage = stage