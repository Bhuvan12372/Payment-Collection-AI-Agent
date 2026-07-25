"""
llm_client.py — ONE single model call, ONE structured output schema.

This demonstrates a production-grade pattern for fuzzy extraction. Instead of
using the raw Groq SDK and manually parsing tool calls, this uses LangChain's
`init_chat_model` for provider agnosticism (easily swap to Anthropic/OpenAI) 
and `.with_structured_output` to securely parse the LLM's response into a 
strictly typed Pydantic model.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from tenacity import Retrying, stop_after_attempt, wait_exponential, retry_if_exception_type
# Using LangChain to remain provider-agnostic
try:
    from langchain.chat_models import init_chat_model
    from langchain_core.messages import SystemMessage, HumanMessage
except ImportError:
    # If langchain is missing, fail gracefully or inform the user
    pass


def load_env_file() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()


GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")


class ExtractionSchema(BaseModel):
    """
    Extract any of these fields that are EXPLICITLY present in the user's message.
    Omit any field not present — never guess or invent a value.
    """
    full_name: Optional[str] = Field(
        None, description="The stated full/legal name. If a nickname and full name appear, prefer the full name."
    )
    dob: Optional[str] = Field(
        None, description="Date of birth normalized to YYYY-MM-DD, only if dateutil parsing would clearly fail on raw phrasing."
    )
    amount: Optional[float] = Field(None, description="The payment amount specified by the user.")
    wants_full_balance: Optional[bool] = Field(
        None, description="True if the user wants to pay their entire outstanding balance rather than a specific number."
    )
    cvv: Optional[str] = Field(
        None, description="3-4 digit CVV, converting any spelled-out digits (e.g. 'one two three') to numerals."
    )
    cardholder_name: Optional[str] = Field(
        None, description="Name on the card, ONLY if explicitly stated as different from the account holder's name."
    )
    card_number: Optional[str] = Field(
        None, description="The 16 digit card number, ONLY if explicitly stated."
    )
    card_expiry: Optional[str] = Field(
        None, description="The card expiry in MM/YYYY format."
    )


class LLMClient:
    """Production-grade LLM wrapper."""

    def __init__(self, api_key: Optional[str] = None, model: str = GROQ_MODEL):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.model_name = model

    def extract_name(self, text: str) -> Optional[str]:
        """Dedicated prompt for extracting a full name with few-shot examples."""
        if not self.api_key:
            print("\n[Error: GROQ_API_KEY is not set in environment!]\n")
            return None

        try:
            from langchain.chat_models import init_chat_model
            from langchain_core.messages import SystemMessage, HumanMessage
        except ImportError:
            print("\n[Error: langchain is not installed!]\n")
            return None

        system_prompt = (
            "You extract the user's full legal name from their message.\n"
            "If no name is provided, output exactly: NONE\n"
            "Do not output anything other than the name itself.\n\n"
            "Examples:\n"
            'Instead of "my name is Nithin Jain" Expect Nithin Jain\n'
            'Instead of "it\'s Nithin, Nithin Jain" Expect Nithin Jain\n'
            'Instead of "Rajarajeswari Balasubramaniam" Expect Rajarajeswari Balasubramaniam\n'
            'Instead of "you can call me Raja but my full name is Rajarajeswari Balasubramaniam" Expect Rajarajeswari Balasubramaniam\n'
            'Instead of "what is my balance?" Expect NONE'
        )

        try:
            for attempt in Retrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                retry=retry_if_exception_type(Exception)
            ):
                with attempt:
                    llm = init_chat_model(self.model_name, model_provider="groq", temperature=0, api_key=self.api_key)
                    result = llm.invoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=text)
                    ])
                    extracted = result.content.strip()
                    if extracted == "NONE" or not extracted:
                        return None
                    return extracted
        except Exception as e:
            print(f"\n[LLM Name Extraction Error after retries: {e}]\n")
            return None

    def extract_all(self, text: str, currently_awaiting: str = "unknown") -> dict:
        if not self.api_key:
            print("\n[Error: GROQ_API_KEY is not set in environment]\n")
            return {}
            
        try:
            from langchain.chat_models import init_chat_model
            from langchain_core.messages import SystemMessage, HumanMessage
        except ImportError:
            print("\n[Error: langchain is not installed!]\n")
            return {}
            
        system_prompt = (
            "You extract structured fields from a user's message in a "
            "payment-verification chat. Only extract fields explicitly "
            "present. Still extract any OTHER fields present even if out of order.\n\n"
            "Examples of correct extraction:\n"
            "- 'CVV is one two three' -> cvv: '123'\n"
            "- 'just clear the full amount' -> wants_full_balance: true, amount: null\n"
            "- 'can I do 500 for now?' -> amount: 500\n"
            "- 'I was born on 14th May 1990' -> dob: '1990-05-14'"
        )

        # Build dynamic context for the HumanMessage so the SystemMessage remains static for caching
        dynamic_context = f"[Context: Agent is awaiting {currently_awaiting}]\n"
        
        if currently_awaiting == "AWAITING_AMOUNT":
            dynamic_context += (
                "Target Fields:\n"
                "- amount: The payment amount specified by the user.\n"
                "- wants_full_balance: True if the user wants to pay their entire outstanding balance rather than a specific number.\n"
            )
        elif currently_awaiting == "AWAITING_CARD_DETAILS":
            dynamic_context += (
                "Target Fields:\n"
                "- card_number: 12-19 digit card number if explicitly provided.\n"
                "- card_expiry: Card expiration date in MM/YYYY format.\n"
                "- cvv: 3 digit CVV, converting any spelled-out digits (e.g. 'one two three') to numerals.\n"
                "- cardholder_name: Name on the card, ONLY if explicitly stated as different from the account holder's name.\n"
            )
        elif currently_awaiting == "AWAITING_SECONDARY_FACTOR":
            dynamic_context += (
                "Target Fields:\n"
                "- dob: Date of birth normalized to YYYY-MM-DD, only if dateutil parsing would clearly fail on raw phrasing.\n"
            )

        human_content = f"{dynamic_context}\nUser: {text}"

        try:
            for attempt in Retrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                retry=retry_if_exception_type(Exception)
            ):
                with attempt:
                    llm = init_chat_model(self.model_name, model_provider="groq", temperature=0, api_key=self.api_key)
                    extractor = llm.with_structured_output(ExtractionSchema)
                    result = extractor.invoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=human_content)
                    ])
                    return result.model_dump(exclude_none=True) if hasattr(result, 'model_dump') else result.dict(exclude_none=True)
        except Exception as e:
            print(f"\n[LLM Extraction Error after retries: {e}]\n")
            return {}

default_client = LLMClient()