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
from .models import RecommendationsResponse

load_dotenv()

# ============================================================================
# DEBUG CONFIGURATION
# ============================================================================
# Writes each LLM exchange to session_data/<session_id>/ (raw_response.txt and
# cleaned_response.json). Off by default so a normal run leaves no artifacts;
# set SAVE_DEBUG_FILES=true in .env when you need them - notably before using
# scripts/replay_report.py, which replays cleaned_response.json and has nothing
# to read without this enabled.
SAVE_DEBUG_FILES = os.getenv("SAVE_DEBUG_FILES", "false").strip().lower() in ("1", "true", "yes")

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
# Gemini is tried FIRST, ahead of Groq: its free-tier quota is a separate account
# entirely, so spending it first preserves the shared Groq daily/hourly limit for
# when Gemini is exhausted or fails. Chosen over other free tiers because our prompts
# run 8k-30k input tokens, which Cerebras' free 8k context window can't hold at all.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# The local model, named once. It was a bare 'qwen2.5' literal in two places inside
# _send_prompt_ollama; telemetry needs to report which model answered, and a third
# copy of the string would be a third place to forget to change.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5")

# Number of validation-failure retries (each retry is a full extra generation, so
# each one spends more of the hosted daily/hourly quota). Default 0 = a single
# generation attempt per analysis, no self-correction. Raise via the env var - e.g.
# LLM_MAX_RETRIES=1 - to trade quota for one correction round when needed.
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "0"))

# ============================================================================
# CLIENTS
# ============================================================================
# Constructed once at import (guarded on the key being present) rather than per
# request - a new Groq()/genai.Client() on every call is needless setup overhead.
_GROQ_CLIENT = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
_GEMINI_CLIENT = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# JSON Schema for the expected response, used for provider-side structured (schema-
# constrained) decoding. Now that the response has no open-ended dicts or unions
# (see models.Aggregation / join_keys), the whole shape is expressible as a schema,
# so the model can be made to emit schema-valid JSON at generation time instead of
# us extracting/repairing/re-validating it after the fact.
try:
    _RESPONSE_JSON_SCHEMA = RecommendationsResponse.model_json_schema()
except Exception as _schema_err:  # pragma: no cover - defensive
    print(f"[AI_Engine] Could not build response JSON schema ({_schema_err}); "
          f"falling back to plain JSON mode")
    _RESPONSE_JSON_SCHEMA = None

# ============================================================================


def send_prompt(prompt, session_id=None, system_prompt=None, meta=None):
    """
    Args:
        prompt: The user/data portion of the prompt to send to the AI
        session_id: If given, debug files are saved into this existing
            session_data/<session_id>/ folder instead of a new one
        system_prompt: The static instruction block (report guidance + output
            contract). Sent as a separate system message so it forms a stable
            prefix across requests - identical every call, it is what providers
            can prefix-cache, and it keeps the "how to think" text out of the
            per-dataset user message.
        meta: Optional dict, filled in with which provider actually answered.

            This function used to return only the text, so a caller wanting to
            record the provider had nothing to read and could only log AI_BACKEND
            - which is the provider that was *configured*, not the one that
            answered, and is therefore silently wrong every time the ladder below
            falls through. That made the most useful thing about the fallback
            chain (how often the primary is failing) invisible in normal operation.

            An out-param rather than a changed return value so every existing
            caller is unaffected, and rather than a module-level global because
            /api/analyze-full is a sync def that Starlette runs in its threadpool:
            two concurrent analyses genuinely execute at once and would race a
            global, attributing one request's provider to the other. A dict the
            caller allocates per request cannot race.

            Keys set: provider, model, provider_failures, used_fallback.

    Returns:
        Cleaned & parsed JSON object as dict

    Raises:
        ValueError: If response cannot be parsed as valid JSON
    """
    raw_response = None
    provider = None
    model = None
    failures = 0

    if AI_BACKEND == "groq" and not GROQ_API_KEY:
        print("[AI_Engine] AI_BACKEND=groq but GROQ_API_KEY is not set, skipping Groq")

    for label, tier_provider, tier_model, send in _remote_providers():
        try:
            raw_response = send(system_prompt, prompt)
            provider, model = tier_provider, tier_model
            break
        except Exception as e:
            failures += 1
            print(f"[AI_Engine] {label} request failed ({e})")

    if raw_response is None:
        # Local Ollama is the last resort rather than one of the tiers above: it
        # has no quota to exhaust, so it can't fail the way the hosted ones do.
        raw_response = _send_prompt_ollama(system_prompt, prompt)
        provider, model = "ollama", OLLAMA_MODEL

    if meta is not None:
        meta["provider"] = provider
        meta["model"] = model
        meta["provider_failures"] = failures
        # True whenever the first usable tier didn't answer - the number worth
        # watching, because it means quota is running out somewhere.
        meta["used_fallback"] = failures > 0

    print(f"[AI_Engine] Received response from LLM ({provider})")

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
    max_retries: int = None,
    tables: dict = None,
    correction_prompt: str = None,
    system_prompt: str = None,
    meta: dict = None,
) -> dict:
    """
    Like send_prompt, but validates the response against RecommendationsResponse
    (see response_validator.parse_and_validate) before returning it. On
    validation failure, the error is appended to the prompt as correction
    text and the LLM is retried, up to max_retries times. max_retries defaults
    to the LLM_MAX_RETRIES env var (0 = single attempt, no self-correction).

    Returns:
        The validated response as a plain dict (schema_conformant, but still
        report_builder.py's usual dict shape - no callers need to change).

    Raises:
        ValueError: If the response still doesn't validate after exhausting
            retries. Callers should already be catching failures from
            send_prompt (e.g. to fall back to mock data), so this needs no
            new handling on top of that.
    """
    # None means "use the configured default" - keep the parameter so a caller can
    # still force a specific count, but let the LLM_MAX_RETRIES env var drive it
    # (default 0) so quota usage can be tuned without touching code.
    if max_retries is None:
        max_retries = LLM_MAX_RETRIES

    attempt_prompt = prompt
    last_error = None

    for attempt in range(max_retries + 1):
        # system_prompt stays constant across attempts (it's the cacheable prefix);
        # only the user/data attempt_prompt grows with correction feedback below.
        raw_response = send_prompt(
            attempt_prompt, session_id=session_id, system_prompt=system_prompt, meta=meta
        )
        if meta is not None:
            # Each pass is a full extra generation against the hosted quota, so the
            # count matters on its own - and it is recorded before validation, so a
            # run that exhausts its retries and raises still reports what it spent.
            meta["llm_attempts"] = attempt + 1
            meta.setdefault("validation_failures", 0)
        try:
            parsed = parse_and_validate(raw_response, valid_filenames, tables)
            return parsed.model_dump(exclude_none=True)
        except ValueError as e:
            last_error = e
            if meta is not None:
                meta["validation_failures"] = meta.get("validation_failures", 0) + 1
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

    Each entry is (label, provider, model, callable taking (system, user) and
    returning raw text). `label` is the human string for the console; `provider` and
    `model` are the machine-readable pair telemetry records, kept separate so a
    breakdown groups by "gemini" rather than by a sentence that changes whenever the
    log wording is edited. A tier is only included when it's actually usable, so a
    missing key is a skipped tier rather than a guaranteed exception on every request.

    Order: Gemini first, then Groq, then (in send_prompt) local Ollama. Gemini is a
    separate account, so spending it first preserves the Groq daily/hourly quota that
    both Groq models share - and the two Groq tiers only differ by a per-MODEL limit,
    so the fallback model just buys one more Groq attempt before dropping to Ollama.
    """
    providers = []

    if GEMINI_API_KEY:
        providers.append((
            f"Gemini model '{GEMINI_MODEL}'", "gemini", GEMINI_MODEL,
            lambda s, u: _send_prompt_gemini(s, u, GEMINI_MODEL),
        ))

    if AI_BACKEND == "groq" and GROQ_API_KEY:
        providers.append((
            f"Groq model '{GROQ_MODEL}'", "groq", GROQ_MODEL,
            lambda s, u: _send_prompt_groq(s, u, GROQ_MODEL),
        ))
        if GROQ_FALLBACK_MODEL and GROQ_FALLBACK_MODEL != GROQ_MODEL:
            providers.append((
                f"Groq fallback model '{GROQ_FALLBACK_MODEL}'", "groq", GROQ_FALLBACK_MODEL,
                lambda s, u: _send_prompt_groq(s, u, GROQ_FALLBACK_MODEL),
            ))

    return providers


def _chat_messages(system_prompt, user_prompt):
    """Build an OpenAI/Ollama-style messages list, prepending the system message
    only when one is given so the older single-string callers still work."""
    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_prompt})
    return messages


def _send_prompt_ollama(system_prompt, user_prompt) -> str:
    """Local Ollama call. Prefers schema-constrained decoding (Ollama structured
    outputs: format=<json schema>) so qwen2.5 emits a schema-valid object directly.
    Falls back to plain format='json' if this Ollama build can't compile the schema -
    the model is local, so the extra attempt costs no quota. format='json' still
    guarantees syntactically valid JSON, catching most 'wrapped in prose' failures."""
    messages = _chat_messages(system_prompt, user_prompt)

    if _RESPONSE_JSON_SCHEMA is not None:
        try:
            response: ChatResponse = chat(
                model=OLLAMA_MODEL,
                format=_RESPONSE_JSON_SCHEMA,
                options={'temperature': 0},
                messages=messages,
            )
            return response.message.content
        except Exception as e:
            print(f"[AI_Engine] Ollama schema-constrained decode failed ({e}); "
                  f"retrying with plain JSON mode")

    response = chat(
        model=OLLAMA_MODEL,
        format='json',
        options={'temperature': 0},
        messages=messages,
    )
    return response.message.content


def _send_prompt_groq(system_prompt, user_prompt, model: str) -> str:
    # Kept on json_object (not json_schema structured outputs): schema support on
    # Groq varies by model, and a call that a model rejects still burns a request
    # against the daily/hourly quota this whole fallback chain exists to conserve.
    # The Pydantic validation + json_repair safety net downstream covers Groq.
    response = _GROQ_CLIENT.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        temperature=0,
        messages=_chat_messages(system_prompt, user_prompt),
    )
    return response.choices[0].message.content


def _send_prompt_gemini(system_prompt, user_prompt, model: str) -> str:
    """Gemini equivalent of _send_prompt_groq.

    response_mime_type is the JSON-mode switch, same role as Groq's
    response_format={"type": "json_object"}. The static instruction block is passed
    as system_instruction (Gemini's system-message equivalent) so it stays a stable
    cacheable prefix, separate from the per-dataset user content.

    response_schema now enforces RecommendationsResponse's shape server-side. This
    became possible once `aggregations` stopped being an open dict and `join_keys`
    stopped being a Union (see models.Aggregation / models.JoinKeyPair) - the schema
    is fully expressible. Pydantic validation still runs downstream (it also does the
    filename/join cross-checks the schema can't), but the model now emits conforming
    JSON directly instead of us repairing it.
    """
    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0,
        system_instruction=system_prompt or None,
    )
    if _RESPONSE_JSON_SCHEMA is not None:
        # Pass the Pydantic model; the SDK derives the response schema from it.
        config.response_schema = RecommendationsResponse

    response = _GEMINI_CLIENT.models.generate_content(
        model=model,
        contents=user_prompt,
        config=config,
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