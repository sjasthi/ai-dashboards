from ollama import chat
from ollama import ChatResponse
from json_repair import loads as json_repair_loads


def send_prompt(prompt):
    """
    Args:
        prompt: The prompt text to send to the AI
    
    Returns:
        Cleaned & parsed JSON object as dict
    
    Raises:
        ValueError: If response cannot be parsed as valid JSON
    """
    response: ChatResponse = chat(model='qwen2.5', messages=[
    {
        'role': 'user',
        'content': prompt,
    },
    ])

    raw_response = response.message.content
    print(f"[AI_Engine] Received response from LLM")
    
    # Extract and clean JSON
    cleaned_response = _extract_and_clean_json(raw_response)
    
    print(f"[AI_Engine] Response successfully parsed and cleaned")
    return cleaned_response



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