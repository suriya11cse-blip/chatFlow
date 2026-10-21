# chat_ai/services/groq_service.py
"""
Groq API wrapper — AI chat, summarize, smart reply, translate
"""
import os
import json
from django.conf import settings


_client = None


def _get_client():
    global _client
    if _client is None:
        from groq import Groq
        api_key = getattr(settings, 'GROQ_API_KEY', '') or os.getenv('GROQ_API_KEY', '')
        if not api_key:
            raise ValueError("GROQ_API_KEY not configured")
        _client = Groq(api_key=api_key)
    return _client


def chat_completion(messages, model=None, temperature=0.7, max_tokens=1024):
    try:
        client = _get_client()
        model = model or getattr(settings, 'GROQ_MODEL', 'openai/gpt-oss-20b')
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[GROQ ERROR] {e}")
        return f"⚠️ AI error: {str(e)[:100]}"


def chat_completion_json(messages, model=None, temperature=0.3, max_tokens=500):
    raw = chat_completion(messages, model=model, temperature=temperature, max_tokens=max_tokens)
    try:
        raw = raw.strip()
        if raw.startswith('```'):
            lines = raw.split('\n')
            raw = '\n'.join(lines[1:-1]) if len(lines) > 2 else raw
        return json.loads(raw)
    except Exception as e:
        print(f"[JSON PARSE ERROR] {e} | raw={raw[:200]}")
        return None