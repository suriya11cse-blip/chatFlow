# chat_ai/services/summarize.py
"""
Chat summarize logic
"""
from .groq_service import chat_completion


def summarize_messages(messages, max_words=150):
    if not messages:
        return "No messages to summarize."

    formatted = "\n".join([
        f"{m.get('sender', 'Unknown')}: {m.get('content', '')}"
        for m in messages if m.get('content')
    ])

    if not formatted.strip():
        return "No text messages to summarize."

    prompt = f"""Summarize the following chat conversation in {max_words} words or less.
Keep important points, decisions, and action items.
Reply in the same language as the chat.

Chat:
{formatted}

Summary:"""

    return chat_completion(
        [
            {"role": "system", "content": "You are a chat summarizer. Be concise and clear."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=400,
    )


def summarize_group_messages(messages, group_name, max_words=150):
    if not messages:
        return "No messages to summarize."

    formatted = "\n".join([
        f"{m.get('sender', 'Unknown')}: {m.get('content', '')}"
        for m in messages if m.get('content')
    ])

    prompt = f"""Summarize this group chat "{group_name}" in {max_words} words.
Focus on key decisions, action items, and important announcements.

Chat:
{formatted}

Summary:"""

    return chat_completion(
        [
            {"role": "system", "content": "You are a group chat summarizer."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=400,
    )