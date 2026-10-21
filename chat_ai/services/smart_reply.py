# chat_ai/services/smart_reply.py
"""
Smart reply suggestions
"""
from .groq_service import chat_completion_json


def generate_smart_replies(last_messages, num_replies=3):
    if not last_messages:
        return []

    formatted = "\n".join([
        f"{m.get('sender', 'Unknown')}: {m.get('content', '')}"
        for m in last_messages if m.get('content')
    ])

    prompt = f"""Given this recent chat, suggest {num_replies} short replies.
Each reply should be under 8 words, natural, and conversational.
Return ONLY a valid JSON array of strings. No explanation, no markdown.

Chat:
{formatted}

JSON array of {num_replies} replies:"""

    result = chat_completion_json(
        [
            {"role": "system", "content": "You output only valid JSON arrays of strings."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.8,
        max_tokens=200,
    )

    if isinstance(result, list):
        return [str(r)[:80] for r in result[:num_replies]]
    return []