# chat_ai/views.py
"""
ChatFlow — AI Features Views
App: chat_ai
"""
import json
from datetime import timedelta

from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from django.core.cache import cache

from chat.models import Message, ChatRoom, ChatRoomMember
from django.contrib.auth.models import User

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


# ============================================================
# AI CHAT
# ============================================================
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
    conv_id = data.get('conversation_id')

    if not user_text:
        return JsonResponse({'status': 'fail', 'message': 'Empty message'})

    if conv_id:
        try:
            conv = AIConversation.objects.get(id=conv_id, user=request.user)
        except AIConversation.DoesNotExist:
            conv = AIConversation.objects.create(user=request.user, title=user_text[:50])
    else:
        conv = AIConversation.objects.create(user=request.user, title=user_text[:50])

    AIMessage.objects.create(conversation=conv, role='user', content=user_text)

    history = []
    for m in conv.messages.all().order_by('created_at')[:20]:
        history.append({'role': m.role, 'content': m.content})

    system_prompt = (
        "You are ChatFlow AI, a friendly and helpful assistant inside ChatFlow — "
        "a real-time chat app. Keep replies short, conversational, and use emojis occasionally. "
        "Reply in the same language the user writes in (Tamil, English, or Tanglish)."
    )

    messages = [{'role': 'system', 'content': system_prompt}] + history
    reply = groq_service.chat_completion(messages, temperature=0.7, max_tokens=500)

    AIMessage.objects.create(conversation=conv, role='assistant', content=reply)

    return JsonResponse({
        'status': 'ok',
        'reply': reply,
        'conversation_id': conv.id,
    })


@login_required
def ai_conversations_list(request):
    convs = AIConversation.objects.filter(user=request.user)[:50]
    data = [{
        'id': c.id,
        'title': c.title,
        'updated_at': c.updated_at.isoformat(),
        'message_count': c.messages.count(),
    } for c in convs]
    return JsonResponse({'status': 'ok', 'conversations': data})


@login_required
def ai_conversation_detail(request, conv_id):
    try:
        conv = AIConversation.objects.get(id=conv_id, user=request.user)
    except AIConversation.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not found'})

    msgs = [{
        'role': m.role,
        'content': m.content,
        'created_at': m.created_at.isoformat(),
    } for m in conv.messages.all()]

    return JsonResponse({
        'status': 'ok',
        'conversation': {
            'id': conv.id,
            'title': conv.title,
            'messages': msgs,
        },
    })


# ============================================================
# SUMMARIZE
# ============================================================
@login_required
@require_POST
def summarize_chat(request):
    if not _rate_limit(request.user.id, 'summarize', limit=10, window=60):
        return JsonResponse({'status': 'fail', 'message': 'Too many requests.'})

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    other_user_id = data.get('user_id')
    room_id = data.get('room_id')
    limit = int(data.get('limit', 100))

    if not other_user_id and not room_id:
        return JsonResponse({'status': 'fail', 'message': 'user_id or room_id required'})

    if room_id:
        try:
            room = ChatRoom.objects.get(id=room_id)
        except ChatRoom.DoesNotExist:
            return JsonResponse({'status': 'fail', 'message': 'Group not found'})

        if not ChatRoomMember.objects.filter(room=room, user=request.user).exists():
            return JsonResponse({'status': 'fail', 'message': 'Not a member'})

        msgs = Message.objects.filter(room=room).exclude(
            deleted_for_everyone=True
        ).order_by('-timestamp')[:limit]

        formatted = [{
            'sender': m.sender.username,
            'content': m.content or '[media]',
        } for m in reversed(list(msgs))]

        summary = summarize.summarize_group_messages(formatted, room.name)

        AISummary.objects.create(
            user=request.user,
            room=room,
            summary_text=summary,
            message_count=len(formatted),
        )

        return JsonResponse({
            'status': 'ok',
            'summary': summary,
            'count': len(formatted),
            'source': f'Group: {room.name}',
        })

    try:
        other_user = User.objects.get(id=other_user_id)
    except Exception:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    msgs = Message.objects.filter(
        Q(sender=request.user, receiver=other_user) |
        Q(sender=other_user, receiver=request.user)
    ).exclude(deleted_for_everyone=True).order_by('-timestamp')[:limit]

    formatted = [{
        'sender': m.sender.username,
        'content': m.content or '[media]',
    } for m in reversed(list(msgs))]

    summary = summarize.summarize_messages(formatted)

    AISummary.objects.create(
        user=request.user,
        chat_with=other_user,
        summary_text=summary,
        message_count=len(formatted),
    )

    return JsonResponse({
        'status': 'ok',
        'summary': summary,
        'count': len(formatted),
        'source': f'Chat with {other_user.username}',
    })


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
        msgs = Message.objects.filter(
            Q(sender=request.user, receiver=other_user) |
            Q(sender=other_user, receiver=request.user)
        ).order_by('-timestamp')[:5]
    else:
        return JsonResponse({'status': 'fail', 'message': 'user_id or room_id required', 'replies': []})

    formatted = [{
        'sender': m.sender.username,
        'content': m.content or '',
    } for m in reversed(list(msgs))]

    replies = smart_reply.generate_smart_replies(formatted)
    return JsonResponse({'status': 'ok', 'replies': replies})


# ============================================================
# TRANSLATE
# ============================================================
@login_required
@require_POST
def translate_message(request):
    if not _rate_limit(request.user.id, 'translate', limit=30, window=60):
        return JsonResponse({'status': 'fail', 'message': 'Too many requests.'})

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
        return JsonResponse({
            'status': 'ok',
            'translation': result,
            'target': target,
            'source_detected': translate.detect_language(text),
        })
    except Exception as e:
        return JsonResponse({'status': 'fail', 'message': str(e)[:200]})


# ============================================================
# IMAGE GENERATION
# ============================================================
@login_required
@require_POST
def generate_image(request):
    if not _rate_limit(request.user.id, 'imagegen', limit=5, window=60):
        return JsonResponse({'status': 'fail', 'message': 'Too many requests. Wait 1 min.'})

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

        return JsonResponse({
            'status': 'ok',
            'image_url': obj.image.url,
            'id': obj.id,
            'prompt': prompt,
        })
    except Exception as e:
        return JsonResponse({'status': 'fail', 'message': f'Image gen failed: {str(e)[:200]}'})


# ============================================================
# VOICE
# ============================================================
@login_required
@require_POST
def voice_to_text(request):
    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'status': 'fail', 'message': 'No audio file'})

    try:
        text = sarvam_service.speech_to_text(
            audio_file,
            language=getattr(settings, 'SARVAM_STT_LANGUAGE', 'ta-IN')
        )
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
    if not text:
        return JsonResponse({'status': 'fail', 'message': 'text required'})

    try:
        audio_b64 = sarvam_service.text_to_speech(
            text,
            language=getattr(settings, 'SARVAM_TTS_LANGUAGE', 'ta-IN'),
            speaker=getattr(settings, 'SARVAM_TTS_SPEAKER', 'anushka'),
        )
        if not audio_b64:
            return JsonResponse({'status': 'fail', 'message': 'No audio returned'})
        return JsonResponse({'status': 'ok', 'audio_base64': audio_b64, 'format': 'wav'})
    except Exception as e:
        return JsonResponse({'status': 'fail', 'message': str(e)[:200]})


# ============================================================
# GROUP BOT
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

    if not room_id or not command:
        return JsonResponse({'status': 'fail', 'message': 'room_id and command required'})

    if not group_assistant.is_bot_command(command):
        return JsonResponse({'status': 'fail', 'message': 'Not a bot command'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    if not ChatRoomMember.objects.filter(room=room, user=request.user).exists():
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    recent = Message.objects.filter(room=room).order_by('-timestamp')[:20]
    formatted = [{
        'sender': m.sender.username,
        'content': m.content or '',
    } for m in reversed(list(recent))]

    reply = group_assistant.handle_group_command(
        command, formatted, room.name, request.user.username
    )

    return JsonResponse({'status': 'ok', 'reply': reply})


# ============================================================
# AI SETTINGS
# ============================================================
@login_required
def get_ai_settings(request):
    s = _get_or_create_ai_settings(request.user)
    return JsonResponse({
        'status': 'ok',
        'settings': {
            'auto_translate': s.auto_translate,
            'translate_to': s.translate_to,
            'smart_reply_enabled': s.smart_reply_enabled,
            'voice_reply_enabled': s.voice_reply_enabled,
            'group_assistant_enabled': s.group_assistant_enabled,
        }
    })


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
            if isinstance(val, str):
                val = val.lower() in ('1', 'true', 'yes', 'on')
            setattr(s, field, bool(val))

    if 'translate_to' in data:
        s.translate_to = str(data['translate_to'])[:10]

    s.save()
    return JsonResponse({'status': 'ok', 'message': 'Settings updated'})