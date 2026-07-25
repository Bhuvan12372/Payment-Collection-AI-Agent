"""
Translates a ToolError (from tools.py) into a clear, actionable user-facing
message, and classifies it as retryable vs terminal — per the spec:
"Distinguish between user-fixable errors (invalid card) and terminal
failures" and "For retryable errors, guide the user to retry; for terminal
errors, close cleanly".
"""

from tools import ToolError

# error_code -> (user message, is_retryable)
_PAYMENT_ERROR_MESSAGES = {
    "invalid_amount": (
        "That amount isn't valid — it must be a positive number with at "
        "most 2 decimal places.", True,
    ),
    "insufficient_balance": (
        "That amount is more than your outstanding balance.", True,
    ),
    "invalid_card": (
        "That card number couldn't be validated. Please double-check the "
        "card number.", True,
    ),
    "invalid_cvv": (
        "The CVV looks incorrect — it should be 3 digits (4 for Amex).", True,
    ),
    "invalid_expiry": (
        "That card's expiry date is invalid or the card has expired.", True,
    ),
}


def message_for_error(err: ToolError) -> tuple[str, bool]:
    """Returns (user_facing_message, is_retryable)."""
    if err.reason == "not_found":
        return (
            "I couldn't find an account with that ID. Could you double-check it?",
            True,
        )
    if err.reason == "api_error" and err.error_code in _PAYMENT_ERROR_MESSAGES:
        return _PAYMENT_ERROR_MESSAGES[err.error_code]
    if err.reason == "api_error":
        # Documented-but-unmapped error_code — still safe to relay generically.
        return (
            f"The payment couldn't be processed ({err.error_code}). "
            "Please check your details and try again.", True,
        )
    if err.reason == "invalid_payload":
        return (
            "Some of the details provided don't look right. Could you "
            "re-check and resend them?", True,
        )
    if err.reason == "network_error":
        return (
            "I'm having trouble reaching the payment service right now. "
            "Please try again in a moment.", True,
        )
    # unexpected_response — genuinely unknown failure, treat as terminal
    # rather than looping the user on retries that likely won't help.
    return (
        "Something unexpected went wrong on our end and I can't complete "
        "this right now. Please contact support.", False,
    )