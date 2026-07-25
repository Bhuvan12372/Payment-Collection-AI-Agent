# Payment Collection Agent - System Design Document

## Architecture Overview
The Payment Collection Agent is designed as a strict, state-driven conversational CLI application that prioritizes deterministic execution, security, and safe handling of financial data. The architecture is organized around a small set of modules that each play a clear role:

1. **Agent Orchestrator**: The main state machine in agent.py drives the conversation step by step. It ensures that verification happens in the intended order and prevents out-of-order or bypassed flows.
2. **State Management**: state.py stores the current account context, extracted user values, verification attempts, and the account details returned from the lookup API.
3. **Extraction Router**: extraction.py tries deterministic parsing first and only uses the LLM as a fallback for ambiguous natural-language inputs.
4. **Verification Layer**: verification.py contains the core rule-based checks for exact-value matching of the account holder’s name, DOB, Aadhaar last 4 digits, and pincode.
5. **Validation Layer**: validators.py uses Pydantic models to validate known schemas such as account IDs, names, dates, amounts, card numbers, CVV, expiry, and pincode.
6. **Tool Layer**: tools.py contains the only network-facing functions, which call the lookup-account and process-payment endpoints in a deterministic and validated way.
7. **LLM Client**: llm_client.py wraps the LLM integration for name extraction and structured field extraction with a static system prompt, dynamic context injection, and a Pydantic output schema.
8. **User-Facing Error Translation**: error_messages.py maps tool failures into clear, retryable or terminal messages.

## Core Design Principles

### 1. Deterministic Flow First
The workflow is intentionally designed to be highly deterministic. It is not a free-form chatbot. The agent follows a fixed path:
- extract or ask for the account ID,
- look up the account,
- verify the account holder’s full name,
- then verify one of the secondary factors (DOB, Aadhaar last 4, or pincode),
- then collect payment details and submit the payment.

This avoids relying on the LLM to make high-risk business decisions in a financial workflow.

### 2. Regex-Based Extraction for Structured Inputs
The system uses regex wherever the input shape is known and strict. Examples include:
- account ID matching the format ACC followed by digits,
- card number extraction,
- pincode extraction,
- Aadhaar last-4 extraction,
- expiry/date patterns.

This is especially important for fields that must be exact, such as account IDs and card details.

### 3. LLM as a Narrow Fallback
The LLM is not used as the primary parser for every field. It is used only when the input is ambiguous or expressed in natural language. This keeps the workflow robust and avoids unnecessary latency and hallucinations.

## Account Verification Workflow

### Account ID Extraction
The account ID is treated as a structured field with a known format. The system expects it to:
- start with ACC,
- have a fixed length pattern after the prefix,
- be extracted using regex from the user’s message.

This means the workflow is not dependent on an LLM to recognize an account ID reliably.

### Secondary Verification Flow
Once the account details are fetched, the system stores key values such as:
- full name,
- date of birth,
- Aadhaar last 4 digits,
- pincode.

The second-level verification asks the user for their full name first. The logic checks the provided name directly against the stored account full name from state. If it matches exactly, the flow moves forward. If it does not match, the agent allows a limited retry count, currently up to 3 attempts.

After that, the flow can ask for one of the remaining secondary factors from the set:
- DOB,
- Aadhaar last 4,
- pincode.

The verification logic for these checks is contained in verification.py and is intentionally strict. It uses direct value comparison rather than fuzzy matching.

## Validation Strategy

### Known-Schema Validation with Pydantic
The validation layer in validators.py is responsible for checking known input structures before they are used further in the workflow. It validates:
- account ID format,
- name shape,
- DOB format and logical date range,
- Aadhaar last 4 digits,
- pincode as exactly 6 digits,
- amount positivity and decimal precision,
- card number shape,
- CVV length,
- expiry month/year validity.

The goal is to reject malformed inputs early and avoid sending bad data into later processing or API calls.

## Tool-Based API Design

The system uses two primary tool functions in tools.py:

1. **lookup_account**
   - calls the lookup-account endpoint,
   - validates the request input,
   - parses the returned account data,
   - stores the account details in state for later verification.

2. **process_payment**
   - validates the payment payload locally before sending anything,
   - constructs a deterministic request body,
   - sends the payment request,
   - translates the API response into a typed success or failure outcome.

Because this workflow is highly deterministic, the agent should invoke these tools directly based on the current state rather than asking the LLM to decide whether to call them. This is more robust and less error-prone than asking a model to orchestrate a fixed business process.

## State Management Role

state.py is the main memory of the agent. It keeps the following information:
- current conversation stage,
- account ID,
- fetched account details,
- user-provided verification values,
- retry counters for verification steps,
- pending payment and card details.

This centralized state allows the workflow to remain explicit and easy to reason about, while also preventing the agent from losing track of the verification context between turns.

## Error Handling and Messaging

The module error_messages.py is responsible for translating low-level tool failures into user-facing messages. It works by taking the structured ToolError from tools.py and turning it into:
- a human-readable message,
- a retryability decision.

Examples include:
- account not found -> prompt the user to re-check the account ID,
- invalid card or invalid CVV -> ask the user to re-enter details,
- network problems -> tell the user to try again later,
- unexpected or unknown failures -> end the flow safely rather than looping forever.

This separation keeps the business logic and the user experience layer clean and maintainable.

## LLM Client Design

The LLM client in llm_client.py is intentionally lightweight and focused. It is used for two narrow tasks:
- extracting a full name from a user message,
- extracting structured fields from a message when regex alone is not enough.

### Prompt Strategy
The client uses a static system prompt for the overall role and a dynamic human message that adds the current context. This preserves a stable prompt template while still letting the agent tell the LLM what it is currently looking for.

### Prompt Caching
To maintain prompt efficiency, the system prompt remains static and reusable. The context-specific details are injected into the human message instead of changing the system prompt every turn. This approach helps preserve caching behavior and reduces unnecessary prompt churn.

### Pydantic Schema Output
Structured extraction is done through a Pydantic schema called ExtractionSchema. This ensures the model output is shaped as structured data instead of free-form text. The schema contains optional fields such as full_name, dob, amount, wants_full_balance, cvv, cardholder_name, card_number, and card_expiry.

### Why This Design Works
Using a Pydantic schema prevents the model from returning malformed or conversational output in places where the agent expects strict values. The combination of deterministic parsing, schema enforcement, and a narrow fallback use of the LLM gives the system a strong balance of reliability and flexibility.

## Current Implementation Notes

The current implementation already reflects the design direction described above:
- account ID is recognized through regex and validated against a known pattern,
- the lookup flow uses deterministic tool calls rather than a model-based decision layer,
- the verification logic is strict and exact-match based,
- secondary verification uses stored account information from state,
- Pydantic validators are used for known field shapes,
- the LLM is used sparingly and only for ambiguous natural-language extraction.

## Rubric Alignment Assessment

The implementation is largely aligned with the evaluation dimensions below:

- System Thinking: Implemented through a clear finite-state flow in agent.py, with explicit stages for greeting, account lookup, verification, amount collection, and card details.
- Context Handling: Implemented through ConversationState in state.py, which stores the account ID, account profile, verification values, retries, and payment context, e.t.c across turns.
- Verification Logic: Implemented through exact-match checks in verification.py and enforced in the agent for full name, DOB, Aadhaar last 4, and pincode. The agent also uses retry limits to stop unsafe loops.
- Tool Usage: Implemented through deterministic tool functions in tools.py for lookup-account and process-payment, with local validation before any network call.
- Failure Handling: Implemented through error_messages.py and the retry/terminal flow in the agent, so failures are translated into clear messages and the session closes gracefully when needed.
- Code Quality: Implemented with separate modules for state, extraction, validation, verification, tool calls, and LLM integration, which keeps the workflow modular and maintainable.
- Evaluation Design: Implemented through evaluation.py with scenarios for happy path, verification failure, payment failure, and edge cases, plus summary metrics for the main workflow.

## Evaluation and Metrics

The automated evaluation script in evaluation.py currently reports a compact set of metrics that are designed to match the document’s core goals:
- account lookup correctness: whether the agent looked up the correct account ID once the user supplied it,
- verification correctness: whether the agent enforced the intended strict verification behavior for name and secondary factors,
- payment correctness: whether the agent only proceeded to payment when the required fields and validation logic were satisfied,
- edge-case handling: whether the agent behaved sensibly for non-trivial cases such as leap-year DOBs, out-of-order inputs, and zero-balance accounts.

These metrics are useful for validating the main workflow.

## What I Would Improve With More Time

In addition to the core product improvements, I would also integrate production-ready observability and monitoring so the AI application can be operated safely at scale. This would include:
- Prometheus for collecting latency, error, retry, and throughput metrics,
- Grafana for dashboards and alerting,
- LangSmith for tracing prompts, tool calls, and model behavior across runs.

This would make the system easier to debug in production and provide much better visibility into both deterministic workflow issues and LLM-related failures.

Code Quality & Automation: Ruff linting and formatting, along with pre-commit hooks, will be integrated to automatically enforce code quality standards, validate code before commits, and ensure consistent formatting across the codebase.