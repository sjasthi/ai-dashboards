from ollama import chat
from ollama import ChatResponse

def send_prompt(prompt):
    response: ChatResponse = chat(model='qwen2.5', messages=[
    {
        'role': 'user',
        'content': prompt,
    },
    ])

    return response.message.content
