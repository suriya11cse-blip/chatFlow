# chat_ai/services/translate.py
"""
Tamil <-> English translation
"""
from .groq_service import chat_completion


LANG_MAP = {
    'en': 'English',
    'ta': 'Tamil',
    'hi': 'Hindi',
    'te': 'Telugu',
    'ml': 'Malayalam',
    'kn': 'Kannada',
}


def translate_text(text, target_lang='en'):
    if not text or not text.strip():
        return ''

    lang_name = LANG_MAP.get(target_lang, 'English')

    prompt = f"""Translate the following text to {lang_name}.
Return ONLY the translated text, no explanation, no quotes.

Text:
{text}

Translation:"""

    return chat_completion(
        [
            {"role": "system", "content": "You are a translator. Output only the translated text."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=500,
    )


def detect_language(text):
    if not text:
        return 'en'
    for ch in text:
        if '\u0B80' <= ch <= '\u0BFF':
            return 'ta'
    return 'en'