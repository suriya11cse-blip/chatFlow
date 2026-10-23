# chat_ai/views.py
"""
ChatFlow — AI Features Views
App: chat_ai
"""
import json
import io
import os
import glob
import tempfile
import subprocess
import requests
import re
from datetime import timedelta

from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.models import User
from django.core.files.uploadedfile import InMemoryUploadedFile

from chat.models import Message, ChatRoom, ChatRoomMember
from .models import AIConversation, AIMessage, AISummary, AIGeneratedImage, AISettings
from .services import groq_service, sarvam_service, summarize, smart_reply, translate, image_gen, group_assistant


# ============================================================
# HELPERS
# ============================================================
def _rate_limit(user_id, key, limit=20, window=60):
    cache_key = f"ai_rate_{key}_{user_id}"
    count = cache.get(cache_key, 0)
    if count >= limit:
        return False
    cache.set(cache_key, count + 1, window)
    return True

def _get_or_create_ai_settings(user):
    obj, _ = AISettings.objects.get_or_create(user=user)
    return obj


@login_required
@require_POST
def ai_chat(request):
    if not _rate_limit(request.user.id, 'chat', limit=30, window=60):
        return JsonResponse({'status': 'fail', 'message': 'Too many requests. Wait a moment.'})

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    user_text = (data.get('message') or '').strip()
    if not user_text:
        return JsonResponse({'status': 'fail', 'message': 'Empty message'})

    # 1. Get or Create Conversation
    conv, created = AIConversation.objects.get_or_create(
        user=request.user,
        defaults={'title': 'ChatFlow AI'}
    )

    AIMessage.objects.create(conversation=conv, role='user', content=user_text)

    history = []
    for m in conv.messages.all().order_by('-created_at')[:10]:
        history.insert(0, {'role': m.role, 'content': m.content})

    # ⚠️ 2. WEB SEARCH (DuckDuckGo API)
    search_result = ""
    try:
        search_url = f"https://api.duckduckgo.com/?q={user_text}&format=json&no_html=1"
        res = requests.get(search_url, timeout=5)
        search_data = res.json()
        
        if search_data.get('AbstractText'):
            search_result = f"Recent info: {search_data['AbstractText']}"
        elif search_data.get('RelatedTopics'):
            topics = [t['Text'] for t in search_data['RelatedTopics'][:3] if 'Text' in t]
            search_result = f"Recent info: {' '.join(topics)}"
    except Exception as e:
        print(f"[Web Search Error] {e}")

    # ⚠️ 3. LANGUAGE DETECT & SYSTEM PROMPT
    is_tamil = bool(re.search(r'[\u0B80-\u0BFF]', user_text))
    
    system_prompt = (
        "You are ChatFlow AI, a friendly assistant. "
        "Current date is October 2026. "
        "Use the latest info provided below to answer the user accurately. "
    )
    
    if search_result:
        system_prompt += f"\n[LATEST DATA]: {search_result}\n"
        
    if is_tamil:
        system_prompt += " The user is speaking in Tamil. You MUST reply in Tamil."
    else:
        system_prompt += " The user is speaking in English. You MUST reply in English."

    messages = [{'role': 'system', 'content': system_prompt}] + history

    # 4. Call Groq API
    try:
        reply = groq_service.chat_completion(
            messages, 
            model="openai/gpt-oss-20b", 
            temperature=0.7, 
            max_tokens=500
        )
    except Exception as e:
        print(f"[AI CHAT ERROR] {e}")
        return JsonResponse({'status': 'fail', 'message': f'AI Error: {str(e)[:100]}'})

    # 5. Save AI Reply
    ai_msg = AIMessage.objects.create(conversation=conv, role='assistant', content=reply)
    conv.save()

    return JsonResponse({
        'status': 'ok',
        'reply': reply,
        'conversation_id': conv.id,
        'ai_message_id': ai_msg.id,
    })




@login_required
def ai_conversations_list(request):
    convs = AIConversation.objects.filter(user=request.user)[:50]
    data = [{'id': c.id, 'title': c.title, 'updated_at': c.updated_at.isoformat(), 'message_count': c.messages.count()} for c in convs]
    return JsonResponse({'status': 'ok', 'conversations': data})


@login_required
def ai_conversation_detail(request, conv_id):
    try:
        conv = AIConversation.objects.get(id=conv_id, user=request.user)
    except AIConversation.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not found'})

    msgs = [{'role': m.role, 'content': m.content, 'created_at': m.created_at.isoformat()} for m in conv.messages.all()]
    return JsonResponse({'status': 'ok', 'conversation': {'id': conv.id, 'title': conv.title, 'messages': msgs}})


# ============================================================
# SUMMARIZE
# ============================================================

# ============================================================
# SMART REPLIES
# ============================================================
@login_required
@require_POST
def get_smart_replies(request):
    if not _rate_limit(request.user.id, 'smartreply', limit=30, window=60):
        return JsonResponse({'status': 'fail', 'message': 'Too many requests.', 'replies': []})

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    other_user_id = data.get('user_id')
    room_id = data.get('room_id')

    if room_id:
        try:
            room = ChatRoom.objects.get(id=room_id)
        except ChatRoom.DoesNotExist:
            return JsonResponse({'status': 'fail', 'replies': []})
        msgs = Message.objects.filter(room=room).order_by('-timestamp')[:5]
    elif other_user_id:
        try:
            other_user = User.objects.get(id=other_user_id)
        except User.DoesNotExist:
            return JsonResponse({'status': 'fail', 'replies': []})
        msgs = Message.objects.filter(Q(sender=request.user, receiver=other_user) | Q(sender=other_user, receiver=request.user)).order_by('-timestamp')[:5]
    else:
        return JsonResponse({'status': 'fail', 'message': 'user_id or room_id required', 'replies': []})

    formatted = [{'sender': m.sender.username, 'content': m.content or ''} for m in reversed(list(msgs))]
    replies = smart_reply.generate_smart_replies(formatted)
    return JsonResponse({'status': 'ok', 'replies': replies})


# ============================================================
# TRANSLATE & IMAGE
# ============================================================
@login_required
@require_POST
def translate_message(request):
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST
    text = (data.get('text') or '').strip()
    target = (data.get('target') or 'en').lower()
    if not text:
        return JsonResponse({'status': 'fail', 'message': 'text required'})
    try:
        result = translate.translate_text(text, target)
        return JsonResponse({'status': 'ok', 'translation': result})
    except Exception as e:
        return JsonResponse({'status': 'fail', 'message': str(e)[:200]})

@login_required
@require_POST
def generate_image(request):
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return JsonResponse({'status': 'fail', 'message': 'prompt required'})
    try:
        content_file = image_gen.generate_image(prompt)
        obj = AIGeneratedImage(user=request.user, prompt=prompt)
        obj.image.save(content_file.name, content_file, save=True)
        return JsonResponse({'status': 'ok', 'image_url': obj.image.url})
    except Exception as e:
        return JsonResponse({'status': 'fail', 'message': f'Image gen failed: {str(e)[:200]}'})


# ============================================================
# VOICE — STT (FFmpeg Direct)
# ============================================================
@login_required
@require_POST
def voice_to_text(request):
    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'status': 'fail', 'message': 'No audio file'})

    ffmpeg_path = None
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    winget_ffmpeg = glob.glob(f"{local_app_data}\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg*\\**\\bin\\ffmpeg.exe", recursive=True)
    
    if winget_ffmpeg:
        ffmpeg_path = winget_ffmpeg[0]
    else:
        for p in [r"C:\ffmpeg\bin\ffmpeg.exe", r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"]:
            if os.path.exists(p):
                ffmpeg_path = p
                break

    if not ffmpeg_path:
        return JsonResponse({'status': 'fail', 'message': 'FFmpeg not found.'})

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.webm') as tmp_in:
            for chunk in audio_file.chunks():
                tmp_in.write(chunk)
            in_path = tmp_in.name
        out_path = in_path + '.wav'

        command = [ffmpeg_path, '-i', in_path, '-ar', '16000', '-ac', '1', '-y', out_path]
        process = subprocess.run(command, capture_output=True, text=True)
        
        if process.returncode != 0:
            os.remove(in_path)
            return JsonResponse({'status': 'fail', 'message': 'Audio conversion failed.'})

        with open(out_path, 'rb') as f:
            wav_data = f.read()
            
        wav_file = InMemoryUploadedFile(file=io.BytesIO(wav_data), field_name='audio', name='voice.wav', content_type='audio/wav', size=len(wav_data), charset=None)

        # Auto Language Detect
        text = sarvam_service.speech_to_text(wav_file, language="en-IN")
        if text and not re.search(r'[a-zA-Z]', text):
            wav_file.seek(0)
            text_ta = sarvam_service.speech_to_text(wav_file, language="ta-IN")
            if text_ta and len(text_ta.strip()) > 1:
                text = text_ta

        os.remove(in_path)
        os.remove(out_path)
        return JsonResponse({'status': 'ok', 'text': text})
        
    except Exception as e:
        return JsonResponse({'status': 'fail', 'message': str(e)[:200]})


@login_required
@require_POST
def text_to_voice(request):
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST
    text = (data.get('text') or '').strip()
    try:
        audio_b64 = sarvam_service.text_to_speech(text, language=getattr(settings, 'SARVAM_TTS_LANGUAGE', 'ta-IN'))
        return JsonResponse({'status': 'ok', 'audio_base64': audio_b64})
    except Exception as e:
        return JsonResponse({'status': 'fail', 'message': str(e)[:200]})


# ============================================================
# GROUP BOT & SETTINGS
# ============================================================
@login_required
@require_POST
def group_bot(request):
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST
    room_id = data.get('room_id')
    command = (data.get('command') or '').strip()
    if not room_id or not group_assistant.is_bot_command(command):
        return JsonResponse({'status': 'fail'})
    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail'})
    recent = Message.objects.filter(room=room).order_by('-timestamp')[:20]
    formatted = [{'sender': m.sender.username, 'content': m.content or ''} for m in reversed(list(recent))]
    reply = group_assistant.handle_group_command(command, formatted, room.name, request.user.username)
    return JsonResponse({'status': 'ok', 'reply': reply})

@login_required
def get_ai_settings(request):
    s = _get_or_create_ai_settings(request.user)
    return JsonResponse({'status': 'ok', 'settings': {'auto_translate': s.auto_translate, 'translate_to': s.translate_to, 'smart_reply_enabled': s.smart_reply_enabled, 'voice_reply_enabled': s.voice_reply_enabled, 'group_assistant_enabled': s.group_assistant_enabled}})

@login_required
@require_POST
def update_ai_settings(request):
    s = _get_or_create_ai_settings(request.user)
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST
    for field in ['auto_translate', 'smart_reply_enabled', 'voice_reply_enabled', 'group_assistant_enabled']:
        if field in data:
            val = data[field]
            if isinstance(val, str): val = val.lower() in ('1', 'true', 'yes', 'on')
            setattr(s, field, bool(val))
    if 'translate_to' in data: s.translate_to = str(data['translate_to'])[:10]
    s.save()
    return JsonResponse({'status': 'ok'})