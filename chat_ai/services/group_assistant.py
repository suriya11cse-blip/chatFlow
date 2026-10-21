# chat_ai/services/group_assistant.py
"""
Group bot — /commands
"""
from .groq_service import chat_completion
from .summarize import summarize_group_messages


COMMANDS = ['/summarize', '/poll', '/remind', '/help', '/ai']


def handle_group_command(command, context_messages, group_name, username):
    cmd = command.strip().split()[0].lower() if command else ''

    if cmd == '/help' or cmd == '/ai':
        return (
            "🤖 **ChatFlow Group Bot**\n\n"
            "Available commands:\n"
            "`/summarize` — Summarize recent messages\n"
            "`/poll Question | Option1 | Option2` — Create a poll\n"
            "`/remind <text>` — Set a reminder\n"
            "`/ai <question>` — Ask AI in group\n"
            "`/help` — Show this help"
        )

    if cmd == '/summarize':
        return "📝 **Chat Summary:**\n\n" + summarize_group_messages(
            context_messages, group_name
        )

    if cmd == '/poll':
        body = command.replace('/poll', '', 1).strip()
        parts = [p.strip() for p in body.split('|')]
        if len(parts) < 3:
            return "❌ Usage: `/poll Question | Option1 | Option2`"
        question = parts[0]
        options = parts[1:]
        opts = "\n".join([f"{i+1}️⃣ {o}" for i, o in enumerate(options)])
        return f"📊 **{question}**\n\n{opts}\n\n_Vote by replying with a number._"

    if cmd == '/remind':
        body = command.replace('/remind', '', 1).strip()
        if not body:
            return "❌ Usage: `/remind Buy milk at 5pm`"
        return f"⏰ Reminder set for @{username}:\n_{body}_\n\n_(Demo — actual reminder not scheduled)_"

    if cmd == '/ai':
        query = command.replace('/ai', '', 1).strip()
        if not query:
            return "❌ Usage: `/ai <your question>`"
        formatted = "\n".join([
            f"{m.get('sender', '')}: {m.get('content', '')}"
            for m in context_messages[-10:]
            if m.get('content')
        ])
        return chat_completion(
            [
                {"role": "system", "content": f"You are a helpful assistant in group '{group_name}'."},
                {"role": "user", "content": f"Chat context:\n{formatted}\n\nQuestion: {query}"},
            ],
            temperature=0.7,
            max_tokens=400,
        )

    return f"❓ Unknown command: `{cmd}`\nType `/help` to see all commands."


def is_bot_command(text):
    if not text:
        return False
    first = text.strip().split()[0].lower()
    return first in COMMANDS