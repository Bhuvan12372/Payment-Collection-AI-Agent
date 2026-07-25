# Payment Collection Agent

A conversational, deterministic-first CLI agent designed to securely collect and verify user identities and process payment collections.

## Prerequisites
- Python 3.9+
- `pydantic`
- `langchain`, `langchain_core`, `langchain-groq` (or other supported providers)
- `tenacity`
- `python-dateutil`
- A valid Groq API Key

## Environment Setup
Before running the agent, you must set your API key as an environment variable so the LLM fallback can function correctly.

For Linux/macOS:
`export GROQ_API_KEY="your_api_key_here"`

For Windows (Command Prompt):
`set GROQ_API_KEY="your_api_key_here"`

For Windows (PowerShell):
`$env:GROQ_API_KEY="your_api_key_here"`

## How to Run the Agent
To start the interactive chat session, run the following command in your terminal from the root directory of the project:
`python -m cli`

## Using the Agent
1. **Initiation**: The agent will prompt you for your Account ID. (e.g., `ACC1001`)
2. **Name Verification**: You will be asked to provide your full legal name for the initial identity check. The agent uses fuzzy matching and LLM extraction to understand conversational inputs like "You can call me Nithin, but my real name is Nithin Jain".
3. **Secondary Verification**: To ensure maximum security, the agent will ask for an additional data point (Date of Birth, Aadhaar last 4 digits, or Pincode). 
4. **Payment Amount**: Once verified, you will be told your outstanding balance and asked how much you'd like to pay. You can specify a numeric amount or use natural phrasing like "clear my full balance".
5. **Card Details**: Finally, you will provide your 16-digit card number, expiry date (MM/YYYY), and CVV. 
6. **Completion**: If the details are valid, the payment will succeed and a transaction ID will be generated.

## Important Notes
- The agent is built with a strict deterministic state machine. If you attempt to skip steps (e.g. providing your credit card before your name is verified), the agent will safely ignore the out-of-order data and continue prompting for the current requirement.
- You have a maximum of 5 retry attempts for invalid payment details (like an expired card or incorrect CVV) before the agent securely terminates the session.
