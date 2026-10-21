# chat_ai/services/image_gen.py
"""
AI image generation (free — Pollinations.ai)
"""
from urllib.parse import quote
import requests
from django.core.files.base import ContentFile


def generate_image(prompt, width=512, height=512):
    if not prompt or not prompt.strip():
        raise ValueError("Prompt required")

    encoded = quote(prompt.strip()[:400])
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true&enhance=true"

    resp = requests.get(url, timeout=90)
    resp.raise_for_status()

    if len(resp.content) < 1000:
        raise ValueError("Image generation failed — empty response")

    filename = f"ai_{prompt.strip()[:30].replace(' ', '_')}.jpg"
    return ContentFile(resp.content, name=filename)