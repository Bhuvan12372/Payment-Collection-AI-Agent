"""
Interactive CLI. Run with:
    python cli.py

Set PAYMENT_AGENT_USE_MOCK=1 to run against the sample accounts without
hitting the real API. Set ANTHROPIC_API_KEY to use the real LLM for
name/amount extraction; otherwise a deterministic mock extractor is used.
"""

import sys
from agent import Agent

def main():
    agent = Agent()
    print("=== Payment Collection Agent (Ctrl+C to quit) ===")
    user_input = ""
    while True:
        result = agent.next(user_input)
        print(f"\nAgent: {result['message']}")
        if agent.state.closed:
            print("\n(session ended)")
            sys.exit(0)
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\n(session ended)")
            break


if __name__ == "__main__":
    main()