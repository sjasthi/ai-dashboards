from ollama import chat
from ollama import ChatResponse
from pathlib import Path

# def __init__(self, cache_dir="app/data/profiles", log_dir="session_data/logs"):
#     self.cache_dir = Path(cache_dir)
#     self.log_dir = log_dir  

def send_prompt(prompt):
    response: ChatResponse = chat(model='qwen2.5', messages=[
    {
        'role': 'user',
        'content': prompt,
    },
    ])

    return response.message.content

# def save_response(self, response: str, filename: str = "response.txt", dir: str = "session_data/logs") -> None:
#     """Save LLM response to log file."""
#     log_dir = Path(dir)
#     log_dir.mkdir(parents=True, exist_ok=True)
#     log_file = log_dir / filename
#     with open(log_file, 'w') as f:
#         f.write(response)
#     print(f"✓ Response saved: {log_file}")
