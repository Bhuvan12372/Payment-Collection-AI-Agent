# Payment Collection Agent

A conversational, deterministic-first CLI agent designed to securely collect and verify user identities and process payment collections.

## Prerequisites
All required libraries and their versions are listed in `requirements.txt`:

so run the command: pip install -r requirements.txt
- A valid Groq API Key



## Environment Setup
Before running the agent, you must set your LLm model API key as an environment variable in .env so the LLM fallback can function correctly.
GROQ_API_KEY="api_key"


## How to Run the Agent
To start the interactive chat session, run the following command in your terminal from the root directory of the project:
`python -m cli`

## How to the run the evaluation metrics

`python -m evaluation` # full suite or `python -m evaluation --quick`  # smoke only

## Using the Agent
1. **Initiation**: The agent will prompt you for your Account ID. (e.g., `ACC1001`)
2. **Name Verification**: You will be asked to provide your full legal name for the initial identity check. The agent uses fuzzy matching and LLM extraction to understand conversational inputs like "You can call me Nithin, but my real name is Nithin Jain".
3. **Secondary Verification**: To ensure maximum security, the agent will ask for an additional data point (Full Name and Date of Birth, Aadhaar last 4 digits, or Pincode). 
4. **Payment Amount**: Once verified, you will be told your outstanding balance and asked how much you'd like to pay. You can specify a numeric amount or use natural phrasing like "clear my full balance".
5. **Card Details**: Finally, you will provide your 16-digit card number, expiry date (MM/YYYY), and CVV. 
6. **Confirmation**: The agent shows the extracted card details and amount, then asks you to confirm (`yes`) or update the details (`no`) before processing the payment.
7. **Completion**: If the details are valid, the payment will succeed and a transaction ID will be generated.

## Important Notes
- You have a maximum of 3 retry attempts for invalid payment details (like an expired card or incorrect CVV) before the agent securely terminates the session.

## API Behaviour Notes

While testing the provided payment API (process-payment), the following was observed:

- Any CVV is accepted when it has exactly 3 digits.
- Expiry is accepted when the date is greater than the current date.

The agent still performs local validation for these fields before calling the API.