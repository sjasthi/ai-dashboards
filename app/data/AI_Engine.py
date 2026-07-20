import os
from typing import Set
from ollama import chat
from ollama import ChatResponse
from json_repair import loads as json_repair_loads
import json
from dotenv import load_dotenv
from groq import Groq
from google import genai
from google.genai import types as genai_types
from .session_manager import SessionManager
from .response_validator import parse_and_validate

load_dotenv()

# ============================================================================
# DEBUG CONFIGURATION
# ============================================================================
# Toggle this to enable/disable saving raw and cleaned responses to files
SAVE_DEBUG_FILES = True  # Set to False to disable file saving

# ============================================================================
# AI BACKEND CONFIGURATION
# ============================================================================
# Set AI_BACKEND=groq in a local .env file to use the Groq API instead of
# local Ollama. If Groq is requested but not usable (no key, request fails),
# this automatically falls back to local Ollama.
AI_BACKEND = os.getenv("AI_BACKEND", "ollama").lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
# Groq's free-tier daily token limit is tracked separately per model, so a
# smaller/lighter model here has its own untouched quota to fall back on when
# GROQ_MODEL gets rate-limited, before giving up to local Ollama.
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant")
# Gemini sits between Groq and Ollama: its free-tier quota is a separate account
# entirely, so it survives a Groq daily limit that both Groq models share an
# account with. Chosen over other free tiers because our prompts run 8k-30k
# input tokens, which Cerebras' free 8k context window can't hold at all.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# ============================================================================


def send_prompt(prompt, session_id=None):
    """
    Args:
        prompt: The prompt text to send to the AI
        session_id: If given, debug files are saved into this existing
            session_data/<session_id>/ folder instead of a new one

    Returns:
        Cleaned & parsed JSON object as dict

    Raises:
        ValueError: If response cannot be parsed as valid JSON
    """
    raw_response = None

    if AI_BACKEND == "groq" and not GROQ_API_KEY:
        print("[AI_Engine] AI_BACKEND=groq but GROQ_API_KEY is not set, skipping Groq")

    for label, send in _remote_providers():
        try:
            raw_response = send(prompt)
            break
        except Exception as e:
            print(f"[AI_Engine] {label} request failed ({e})")

    if raw_response is None:
        # Local Ollama is the last resort rather than one of the tiers above: it
        # has no quota to exhaust, so it can't fail the way the hosted ones do.
        raw_response = _send_prompt_ollama(prompt)

    print(f"[AI_Engine] Received response from LLM")

    # Extract and clean JSON
    cleaned_response = _extract_and_clean_json(raw_response)

    # ===========================
    # Save debug files if enabled
    if SAVE_DEBUG_FILES:
        _save_debug_files(raw_response, cleaned_response, session_id)

    print(f"[AI_Engine] Response successfully parsed and cleaned")
    return cleaned_response
    # ===========================


def get_validated_recommendations(
    prompt: str,
    valid_filenames: Set[str],
    session_id: str = None,
    max_retries: int = 2,
    tables: dict = None,
    correction_prompt: str = None,
) -> dict:
    """
    Like send_prompt, but validates the response against RecommendationsResponse
    (see response_validator.parse_and_validate) before returning it. On
    validation failure, the error is appended to the prompt as correction
    text and the LLM is retried, up to max_retries times.

    Returns:
        The validated response as a plain dict (schema_conformant, but still
        report_builder.py's usual dict shape - no callers need to change).

    Raises:
        ValueError: If the response still doesn't validate after exhausting
            retries. Callers should already be catching failures from
            send_prompt (e.g. to fall back to mock data), so this needs no
            new handling on top of that.
    """
    attempt_prompt = prompt
    last_error = None

    for attempt in range(max_retries + 1):
        raw_response = send_prompt(attempt_prompt, session_id=session_id)
        try:
            parsed = parse_and_validate(raw_response, valid_filenames, tables)
            return parsed.model_dump(exclude_none=True)
        except ValueError as e:
            last_error = e
            print(f"[AI_Engine] Response failed validation (attempt {attempt + 1}/{max_retries + 1}): {e}")
            # Retries reuse the shorter correction prompt when the caller supplied one
            # (data + output contract, minus the analysis guidance) - resending the full
            # prompt every attempt is what makes one failed validation cost double.
            attempt_prompt = (
                f"{correction_prompt or prompt}\n\n"
                f"Your previous response failed validation with this error:\n{e}\n\n"
                f"Return ONLY corrected raw JSON matching the required structure."
            )

    raise last_error


def _remote_providers():
    """The hosted backends to try, in order, before falling back to local Ollama.

    Each entry is (label, callable taking the prompt and returning raw text). A tier
    is only included when it's actually usable, so a missing key is a skipped tier
    rather than a guaranteed exception on every request.

    Order is cheapest-recovery-first: the second Groq model shares an account with
    the first (so it only helps against a PER-MODEL daily limit), while Gemini is a
    separate account whose quota survives Groq being exhausted account-wide.
    """
    providers = []

    if AI_BACKEND == "groq" and GROQ_API_KEY:
        providers.append(
            (f"Groq model '{GROQ_MODEL}'", lambda p: _send_prompt_groq(p, GROQ_MODEL))
        )
        if GROQ_FALLBACK_MODEL and GROQ_FALLBACK_MODEL != GROQ_MODEL:
            providers.append((
                f"Groq fallback model '{GROQ_FALLBACK_MODEL}'",
                lambda p: _send_prompt_groq(p, GROQ_FALLBACK_MODEL),
            ))

    if GEMINI_API_KEY:
        providers.append(
            (f"Gemini model '{GEMINI_MODEL}'", lambda p: _send_prompt_gemini(p, GEMINI_MODEL))
        )

    return providers


def _send_prompt_ollama(prompt: str) -> str:
    """Original local Ollama call. format='json' asks the model to guarantee
    syntactically valid JSON output, catching most "wrapped in prose"
    failures before they ever reach parsing."""
    response: ChatResponse = chat(model='qwen2.5', format='json', messages=[
    {
        'role': 'user',
        'content': prompt,
    },
    ])
    return response.message.content


def _send_prompt_groq(prompt: str, model: str) -> str:
    client = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {
                'role': 'user',
                'content': prompt,
            },
        ],
    )
    return response.choices[0].message.content


def _send_prompt_gemini(prompt: str, model: str) -> str:
    """Gemini equivalent of _send_prompt_groq.

    response_mime_type is the JSON-mode switch, same role as Groq's
    response_format={"type": "json_object"}. Gemini also accepts a response_schema
    that would enforce our shape server-side, but RecommendationsResponse can't be
    expressed in the subset it supports - `aggregations` is an open-ended dict and
    `join_keys` is a Union - so validation stays in response_validator, where those
    are already handled.
    """
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )
    return response.text


def _extract_and_clean_json(text: str) -> dict:
    """Extract JSON object from text and clean up common LLM formatting issues."""
    # Find JSON object boundaries (LLMs often add surrounding text)
    start = text.find('{')
    end = text.rfind('}')
    
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found in AI response: {text[:200]}")
    
    raw_json = text[start:end + 1]
    print(f"[AI_Engine] Raw JSON length: {len(raw_json)} chars")
    
    try:
        return json_repair_loads(raw_json)
    except Exception as e:
        print(f"[AI_Engine] Failed to parse JSON: {str(e)}")
        raise ValueError(f"Failed to parse JSON: {str(e)}")

# ===================
# raw and cleaned json files for testing - move/remove later
# ===================
def _save_debug_files(raw_response: str, cleaned_response: dict, session_id=None) -> None:
    try:
        session = SessionManager(session_id=session_id)
        
        # Save raw response as text
        session.save_response(raw_response, filename="raw_response.txt")
        
        # Save cleaned response as formatted JSON
        cleaned_json = json.dumps(cleaned_response, indent=2, default=str)
        session.save_response(cleaned_json, filename="cleaned_response.json")
        
        print(f"[AI_Engine] Debug files saved to session: {session.get_session_dir()}")
        
    except Exception as e:
        print(f"[AI_Engine] Warning: Failed to save debug files: {str(e)}")