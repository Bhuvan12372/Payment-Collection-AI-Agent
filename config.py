"""
Central configuration for the payment collection agent.

All tunable constants (retry limits, API base URL, model name) live here
so behavior can be adjusted without touching business logic.
"""

import os

# --- External Payment/Verification API ---
API_BASE_URL = os.environ.get(
    "PAYMENT_API_BASE_URL",
    "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com",
)
LOOKUP_ACCOUNT_ENDPOINT = f"{API_BASE_URL}/api/lookup-account"
PROCESS_PAYMENT_ENDPOINT = f"{API_BASE_URL}/api/process-payment"
API_TIMEOUT_SECONDS = 10

# --- LLM (used only for free-form extraction, never for verification decisions) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "claude-sonnet-4-6")

# --- Retry limits (business rule, documented in DESIGN.md) ---
MAX_ACCOUNT_LOOKUP_RETRIES = 3   # wrong/unknown account id attempts
# Name + secondary-factor form ONE combined verification check per spec
# ("Full name matches exactly AND at least one of DOB/Aadhaar/pincode
# matches"), so they share a single retry budget rather than 3 attempts
# each (which would silently allow up to 6 total tries).
MAX_VERIFICATION_RETRIES = 3
MAX_CARD_RETRIES = 3             # retryable card errors (invalid_card/cvv/expiry/amount)

# --- Misc ---
CURRENCY_SYMBOL = "₹"