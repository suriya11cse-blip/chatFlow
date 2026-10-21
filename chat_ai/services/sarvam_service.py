# chat_ai/services/sarvam_service.py
"""
Sarvam AI wrapper — STT + TTS
"""
import os
import requests
from django.conf import settings


BASE_URL = "https://api.sarvam.ai"


def _get_api_key():
    return getattr(settings, 'SARVAM_API_KEY', '') or os.getenv('SARVAM_API_KEY', '')


def speech_to_text(audio_file, language="ta-IN"):
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("SARVAM_API_KEY not configured")

    url = f"{BASE_URL}/speech-to-text"
    headers = {"api-subscription-key": api_key}

    audio_file.seek(0)
    files = {'file': ('audio.wav', audio_file.read(), 'audio/wav')}
    data = {'language_code': language, 'model': 'saarika:v2'}

    resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    return result.get('transcript', '') or result.get('text', '')


def text_to_speech(text, language="ta-IN", speaker="anushka"):
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("SARVAM_API_KEY not configured")

    url = f"{BASE_URL}/text-to-speech"
    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": [text[:500]],
        "target_language_code": language,
        "speaker": speaker,
        "speech_sample_rate": 8000,
        "enable_preprocessing": True,
        "model": "bulbul:v1",
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    audios = result.get('audios', [])
    return audios[0] if audios else None