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

    # ✅ Get original filename and extension (fix for .webm)
    original_name = getattr(audio_file, 'name', 'audio.webm')
    ext = os.path.splitext(original_name)[1].lower()
    if not ext:
        ext = '.webm'
    
    # Map extension to correct MIME type
    mime_map = {
        '.webm': 'audio/webm',
        '.wav': 'audio/wav',
        '.mp3': 'audio/mpeg',
        '.m4a': 'audio/mp4',
        '.ogg': 'audio/ogg',
    }
    mime_type = mime_map.get(ext, 'audio/webm')

    audio_file.seek(0)
    files = {
        'file': (f'audio{ext}', audio_file.read(), mime_type),
    }
    data = {
        'language_code': language,
        'model': 'saarika:v2.5',
    }

    resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)
    
    # ✅ Debugging for 400 error
    if resp.status_code != 200:
        print(f"[SARVAM STT ERROR] Status: {resp.status_code}")
        print(f"[SARVAM STT ERROR] Response: {resp.text}")
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