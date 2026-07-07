import os
from ollama import chat
from ollama import ChatResponse
from json_repair import loads as json_repair_loads
import json
from dotenv import load_dotenv
from groq import Groq
from .session_manager import SessionManager

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

    if AI_BACKEND == "groq" and GROQ_API_KEY:
        try:
            raw_response = _send_prompt_groq(prompt, GROQ_MODEL)
        except Exception as e:
            print(f"[AI_Engine] Groq request failed with model '{GROQ_MODEL}' ({e}), "
                  f"trying fallback model '{GROQ_FALLBACK_MODEL}'")
            try:
                raw_response = _send_prompt_groq(prompt, GROQ_FALLBACK_MODEL)
            except Exception as e2:
                print(f"[AI_Engine] Groq fallback model also failed ({e2}), falling back to local Ollama")
    elif AI_BACKEND == "groq" and not GROQ_API_KEY:
        print("[AI_Engine] AI_BACKEND=groq but GROQ_API_KEY is not set, falling back to local Ollama")

    if raw_response is None:
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


def _send_prompt_ollama(prompt: str) -> str:
    """Original local Ollama call, unchanged."""
    response: ChatResponse = chat(model='qwen2.5', messages=[
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
        messages=[
            {
                'role': 'user',
                'content': prompt,
            },
        ],
    )
    return response.choices[0].message.content


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