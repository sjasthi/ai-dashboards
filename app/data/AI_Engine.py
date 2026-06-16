import anthropic
import os

from dotenv import load_dotenv

load_dotenv()

def get_report_recommendations(summary_text):

    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Create a .env file or set the environment variable."
        )

    client = anthropic.Anthropic(api_key=api_key)

    try:
        message = client.messages.create(
            model="claude-sonnet-4",
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": f"""
You are a business intelligence analyst.

Analyze the datasets below.

Recommend 3 to 5 reports.

For each report provide:

1. Report Title
2. Purpose
3. Columns Used
4. Business Value

Dataset Summary:

{summary_text}
"""
                }
            ]
        )

        return message.content[0].text
    except anthropic.BadRequestError as e:
        # Likely billing/credits issue (400). Return a helpful fallback string.
        print(f"Anthropic BadRequestError: {e}")
        fallback = (
            "Anthropic API error: billing/credit issue detected. "
            "Please replace `ANTHROPIC_API_KEY` with a funded key or add credits.\n\n"
        )
        return fallback
