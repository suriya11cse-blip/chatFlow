# chat/views.py
import os
import uuid
import random
import re
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from datetime import timedelta
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import (
    Message, MessageReaction, UserProfile, ChatSettings,
    FriendRequest, Call, ChatContact,
)
from .consumers import ONLINE_USERS


# ============================================================
# CONSTANTS
# ============================================================
AI_USERNAME = 'ChatFlow AI'          # Change to 'Meta AI' if you want
IMAGE_MAX_BYTES = 100 * 1024 * 1024
VIDEO_MAX_BYTES = 100 * 1024 * 1024
AUDIO_MAX_BYTES = 16 * 1024 * 1024
DOCUMENT_MAX_BYTES = 100 * 1024 * 1024


# ============================================================
# HELPERS
# ============================================================

def _format_wa_time(dt):
    """WhatsApp-style time: '10:45 pm'."""
    if not dt:
        return ''
    h = dt.hour
    m = dt.minute
    ampm = 'am' if h < 12 else 'pm'
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{m:02d} {ampm}"


def _avatar_url(user):
    try:
        p = UserProfile.objects.get(user=user)
        if p.avatar:
            return p.avatar.url
    except UserProfile.DoesNotExist:
        pass
    return ''


def _generate_chatflow_id():
    for _ in range(50):
        part1 = str(random.randint(1000, 9999))
        part2 = str(random.randint(1000, 9999))
        candidate = f"{part1}-{part2}"
        if not UserProfile.objects.filter(chatflow_id=candidate).exists():
            return candidate
    return f"{random.randint(10000000, 99999999)}"


def _ask_ai(user_message, history=None):
    """Call Groq API and return response text."""
    api_key = getattr(settings, 'GROQ_API_KEY', '')
    model = getattr(settings, 'GROQ_MODEL', 'openai/gpt-oss-20b')

    if not api_key:
        return "⚠️ AI service not configured. Please add GROQ_API_KEY."

    try:
        from groq import Groq
        client = Groq(api_key=api_key)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are ChatFlow AI, a friendly, helpful assistant built into ChatFlow — "
                    "a real-time chat app. Keep responses short and conversational (like WhatsApp). "
                    "Use emojis occasionally. Reply in the same language the user writes in "
                    "(Tamil, English, or Tanglish)."
                )
            }
        ]

        if history:
            for h in history[-6:]:
                messages.append(h)

        messages.append({"role": "user", "content": user_message})

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"[AI ERROR] {e}")
        return "😅 Sorry, I'm having trouble responding right now. Try again in a moment."


def _ensure_ai_friend(user):
    """
    Ensure ChatFlow AI is auto-added as friend (Meta AI style).
    Called on every login/home.
    """
    try:
        ai_user = User.objects.get(username=AI_USERNAME)
    except User.DoesNotExist:
        return None

    # Add AI as friend (both directions)
    FriendRequest.objects.get_or_create(
        sender=user, receiver=ai_user,
        defaults={'status': 'accepted'}
    )
    FriendRequest.objects.get_or_create(
        sender=ai_user, receiver=user,
        defaults={'status': 'accepted'}
    )
    ChatContact.objects.get_or_create(user=user, contact=ai_user)
    ChatContact.objects.get_or_create(user=ai_user, contact=user)

    # Welcome message (only first time)
    already = Message.objects.filter(
        sender=ai_user, receiver=user, message_type='text'
    ).exists()
    if not already:
        Message.objects.create(
            sender=ai_user,
            receiver=user,
            content=(
                f"👋 Hi {user.username}! I'm ChatFlow AI, your friendly assistant.\n\n"
                f"Ask me anything — I can help with coding, questions, or just chat!\n\n"
                f"🎤 Try the mic button to speak!"
            ),
            message_type='text',
        )
    return ai_user


# ============================================================
# AUTH
# ============================================================

def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user:
            login(request, user)
            _ensure_ai_friend(user)
            return redirect('home')
        return render(request, 'chat/login.html', {'error': 'Invalid credentials'})
    return render(request, 'chat/login.html')


def register_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        u = (request.POST.get('username') or '').strip()
        p = request.POST.get('password') or ''
        handle = (request.POST.get('handle') or '').strip().lower()

        if not u or not p or not handle:
            return render(request, 'chat/register.html', {'error': 'All fields are required'})

        if User.objects.filter(username=u).exists():
            return render(request, 'chat/register.html', {'error': 'Username already exists'})

        if UserProfile.objects.filter(handle=handle).exists():
            return render(request, 'chat/register.html', {'error': f'Handle @{handle} is already taken'})

        user = User.objects.create_user(username=u, password=p)

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.handle = handle
        if not profile.chatflow_id:
            profile.chatflow_id = _generate_chatflow_id()
        profile.save(update_fields=['handle', 'chatflow_id'])

        _ensure_ai_friend(user)

        return redirect('login')

    return render(request, 'chat/register.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# ============================================================
# SIDEBAR / HOME
# ============================================================

def _last_msg_preview(m):
    if not m:
        return ''
    if m.deleted_for_everyone:
        return 'Message deleted'
    if (m.content or '').startswith('↩️ Replied to status'):
        return '↩️ Replied to your status'
    if m.message_type == 'call_missed':
        return '📞 Missed call'
    if m.message_type == 'call_rejected':
        return '📞 Call declined'
    if m.message_type == 'call_busy':
        return '📞 Call — user busy'
    if m.message_type == 'call_answered':
        return '📞 Call · ' + (m.call_duration or '')
    if m.message_type == 'call_cancelled':
        return '📞 Call cancelled'
    if m.document_url:
        return 'Document'
    if m.image_url:
        return 'Image'
    if m.audio_url:
        return 'Voice message'
    if m.video_url:
        return 'Video'
    return (m.content or '')[:40]


def _get_sidebar_users(user):
    now = timezone.now()

    pairs = FriendRequest.objects.filter(
        Q(sender=user, status='accepted') | Q(receiver=user, status='accepted')
    ).values_list('sender_id', 'receiver_id')

    friend_set = set()
    for s, r in pairs:
        friend_set.add(r if s == user.id else s)
    friend_set.discard(user.id)

    users = list(User.objects.filter(id__in=friend_set))

    contact_flags = {}
    for c in ChatContact.objects.filter(user=user, contact__in=users):
        contact_flags[c.contact_id] = {
            'is_muted': c.is_muted,
            'is_pinned': c.is_pinned,
            'is_archived': c.is_archived,
            'sound_enabled': c.sound_enabled,
        }

    results = []
    for u in users:
        last_msg = Message.objects.filter(
            Q(sender=user, receiver=u) | Q(sender=u, receiver=user)
        ).exclude(deleted_for=user).exclude(
            expires_at__isnull=False, expires_at__lte=now
        ).order_by('-timestamp').first()

        unread = Message.objects.filter(
            sender=u, receiver=user, is_read=False
        ).exclude(deleted_for_everyone=True).exclude(deleted_for=user).exclude(
            expires_at__isnull=False, expires_at__lte=now
        ).count()

        is_ai = False
        try:
            is_ai = bool(u.profile.is_ai_bot)
        except UserProfile.DoesNotExist:
            pass

        # Also treat AI_USERNAME as AI
        if u.username == AI_USERNAME:
            is_ai = True

        flags = contact_flags.get(u.id, {})
        is_muted = flags.get('is_muted', False)
        is_pinned = flags.get('is_pinned', False)
        is_archived = flags.get('is_archived', False)
        sound_enabled = flags.get('sound_enabled', True)

        if is_muted:
            unread = 0

        results.append({
            'id': u.id,
            'username': u.username,
            'avatar_url': _avatar_url(u),
            'last_msg': _last_msg_preview(last_msg),
            'unread': unread,
            'last_ts': last_msg.timestamp if last_msg else None,
            'is_ai_bot': is_ai,
            'is_muted': is_muted,
            'is_pinned': is_pinned,
            'is_archived': is_archived,
            'sound_enabled': sound_enabled,
        })

        # ⭐ WhatsApp-style: pinned first, then by latest message time
    # AI also follows normal sorting (no special priority)
    with_msg = [r for r in results if r['last_ts'] is not None]
    without_msg = [r for r in results if r['last_ts'] is None]
    
    # Sort with_msg: pinned first, then by timestamp (newest first)
    with_msg.sort(key=lambda x: (not x['is_pinned'], -x['last_ts'].timestamp()))
    
    # Sort without_msg: pinned first, then alphabetical (AI still in list)
    without_msg.sort(key=lambda x: (not x['is_pinned'], x['username'].lower()))

    return with_msg + without_msg

@login_required
def home(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not profile.chatflow_id:
        profile.chatflow_id = _generate_chatflow_id()
        profile.save(update_fields=['chatflow_id'])

    profile.last_seen = timezone.now()
    profile.save(update_fields=['last_seen'])

    # ⭐ Auto-add AI on every home load
    _ensure_ai_friend(request.user)

    sidebar = _get_sidebar_users(request.user)

    users_data_final = []
    for ud in sidebar:
        users_data_final.append({
            'user': {'id': ud['id'], 'username': ud['username']},
            'avatar_url': ud['avatar_url'],
            'last_content': ud['last_msg'],
            'unread_count': ud['unread'],
            'is_ai_bot': ud.get('is_ai_bot', False),
            'is_muted': ud.get('is_muted', False),
            'is_pinned': ud.get('is_pinned', False),
            'is_archived': ud.get('is_archived', False),
            'sound_enabled': ud.get('sound_enabled', True),
        })

    my_avatar_url = profile.avatar.url if profile.avatar else ''
    online_user_ids = list(ONLINE_USERS)

    return render(request, 'chat/chat.html', {
        'users_data': users_data_final,
        'current_user': request.user,
        'my_avatar_url': my_avatar_url,
        'online_user_ids': online_user_ids,
    })


# ============================================================
# MESSAGES
# ============================================================

@login_required
def get_messages(request, user_id):
    other = get_object_or_404(User, id=user_id)
    now = timezone.now()

    Message.objects.filter(
        Q(sender=request.user, receiver=other) |
        Q(sender=other, receiver=request.user),
        expires_at__isnull=False,
        expires_at__lte=now
    ).delete()

    Message.objects.filter(
        sender=other, receiver=request.user, is_read=False
    ).update(is_read=True)

    msgs = Message.objects.filter(
        Q(sender=request.user, receiver=other) |
        Q(sender=other, receiver=request.user)
    ).exclude(deleted_for=request.user).exclude(
        expires_at__isnull=False, expires_at__lte=now
    ).order_by('timestamp')

    data = []
    for m in msgs:
        reply_to = None
        if m.reply_to:
            reply_to = {
                'id': m.reply_to.id,
                'sender': m.reply_to.sender.username,
                'content': (m.reply_to.content or '')[:80],
            }

        reactions_summary = {}
        for r in m.reactions.select_related('user').all():
            if r.emoji not in reactions_summary:
                reactions_summary[r.emoji] = {
                    'emoji': r.emoji, 'count': 0, 'users': [], 'me': False
                }
            reactions_summary[r.emoji]['count'] += 1
            reactions_summary[r.emoji]['users'].append(r.user.username)
            if r.user_id == request.user.id:
                reactions_summary[r.emoji]['me'] = True
        reactions_list = list(reactions_summary.values())

        now_local = timezone.localtime(m.timestamp)
        today = timezone.localtime(timezone.now()).date()
        msg_date = now_local.date()
        if msg_date == today:
            date_display = 'TODAY'
        elif msg_date == today - timedelta(days=1):
            date_display = 'YESTERDAY'
        else:
            date_display = now_local.strftime('%d-%m-%Y')

        data.append({
            'id': m.id,
            'sender': m.sender.username,
            'sender_id': m.sender.id,
            'receiver': m.receiver.username if m.receiver else '',
            'receiver_id': m.receiver.id if m.receiver else None,
            'content': '' if m.deleted_for_everyone else (m.content or ''),
            'timestamp': _format_wa_time(now_local),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
            'deleted_for_everyone': m.deleted_for_everyone,
            'is_mine': m.sender == request.user,
            'is_read': m.is_read,
            'is_starred': m.is_starred,
            'is_pinned': getattr(m, 'is_pinned', False),
            'image_url': m.image_url,
            'audio_url': m.audio_url,
            'audio_duration': m.audio_duration,
            'video_url': m.video_url,
            'video_duration': m.video_duration,
            'document_url': m.document_url,
            'document_name': m.document_name,
            'document_size': m.document_size,
            'reply_to': reply_to,
            'forwarded': m.forwarded,
            'reactions': reactions_list,
            'expires_at': m.expires_at.isoformat() if m.expires_at else None,
            'message_type': m.message_type,
            'call_duration': m.call_duration,
            'is_edited': getattr(m, 'is_edited', False),
            'edited_at': _format_wa_time(timezone.localtime(m.edited_at)) if getattr(m, 'edited_at', None) else '',
        })

    return JsonResponse({'messages': data})


@login_required
def search_messages(request, user_id):
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'status': 'ok', 'messages': []})

    msgs = Message.objects.filter(
        Q(sender=request.user, receiver=other) |
        Q(sender=other, receiver=request.user),
        content__icontains=query,
    ).exclude(deleted_for_everyone=True).exclude(
        deleted_for=request.user
    ).order_by('-timestamp')[:50]

    data = []
    for m in msgs:
        now_local = timezone.localtime(m.timestamp)
        today = timezone.localtime(timezone.now()).date()
        msg_date = now_local.date()
        if msg_date == today:
            date_display = 'TODAY'
        elif msg_date == today - timedelta(days=1):
            date_display = 'YESTERDAY'
        else:
            date_display = now_local.strftime('%d-%m-%Y')

        data.append({
            'id': m.id,
            'sender': m.sender.username,
            'sender_id': m.sender.id,
            'content': m.content,
            'timestamp': _format_wa_time(now_local),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
            'is_mine': m.sender == request.user,
        })

    return JsonResponse({'status': 'ok', 'messages': data})


@csrf_exempt
@login_required
def delete_for_me(request, msg_id):
    if request.method == 'POST':
        msg = get_object_or_404(Message, id=msg_id)
        msg.deleted_for.add(request.user)
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'fail'})


@csrf_exempt
@login_required
def delete_for_everyone(request, msg_id):
    if request.method == 'POST':
        msg = get_object_or_404(Message, id=msg_id)
        if msg.sender == request.user:
            msg.deleted_for_everyone = True
            msg.content = ''
            msg.image_url = None
            msg.audio_url = None
            msg.audio_duration = ''
            msg.video_url = None
            msg.video_duration = ''
            msg.document_url = None
            msg.document_name = ''
            msg.document_size = ''
            msg.save()
            return JsonResponse({'status': 'ok'})
        return JsonResponse({'status': 'not_allowed'})
    return JsonResponse({'status': 'fail'})


@csrf_exempt
@login_required
def clear_chat(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    deleted_count, _ = Message.objects.filter(
        Q(sender=request.user, receiver=other) |
        Q(sender=other, receiver=request.user)
    ).delete()

    return JsonResponse({'status': 'ok', 'deleted_count': deleted_count})


@csrf_exempt
@login_required
def toggle_star(request, msg_id):
    if request.method == 'POST':
        msg = get_object_or_404(Message, id=msg_id)
        msg.is_starred = not msg.is_starred
        msg.save()
        return JsonResponse({'status': 'ok', 'starred': msg.is_starred})
    return JsonResponse({'status': 'fail'})


# ============================================================
# PIN MESSAGE
# ============================================================

@csrf_exempt
@login_required
def pin_message(request, msg_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    try:
        msg = Message.objects.get(id=msg_id)
    except Message.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Message not found'})

    if request.user not in (msg.sender, msg.receiver):
        return JsonResponse({'status': 'fail', 'message': 'Not allowed'})

    msg.is_pinned = not getattr(msg, 'is_pinned', False)
    msg.save(update_fields=['is_pinned'])
    return JsonResponse({'status': 'ok', 'is_pinned': msg.is_pinned})


@login_required
def get_pinned_messages(request, user_id):
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    msgs = Message.objects.filter(
        Q(sender=request.user, receiver=other) | Q(sender=other, receiver=request.user),
        is_pinned=True,
        deleted_for_everyone=False,
    ).order_by('-timestamp')[:50]

    data = [{
        'id': m.id,
        'content': m.content or '',
        'sender': m.sender.username,
        'sender_id': m.sender.id,
        'timestamp': _format_wa_time(timezone.localtime(m.timestamp)),
    } for m in msgs]

    return JsonResponse({'status': 'ok', 'messages': data})


# ============================================================
# MEDIA GALLERY
# ============================================================

@login_required
def get_chat_media(request, user_id):
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    media_type = request.GET.get('type', 'media')
    base_qs = Message.objects.filter(
        Q(sender=request.user, receiver=other) | Q(sender=other, receiver=request.user)
    ).exclude(deleted_for=request.user).exclude(deleted_for_everyone=True)

    items = []

    if media_type == 'media':
        for m in base_qs.filter(Q(image_url__isnull=False) | Q(video_url__isnull=False)).order_by('-timestamp')[:200]:
            if m.image_url:
                items.append({'type': 'image', 'url': m.image_url, 'id': m.id})
            if m.video_url:
                items.append({'type': 'video', 'url': m.video_url, 'id': m.id, 'duration': m.video_duration})

    elif media_type == 'docs':
        for m in base_qs.filter(document_url__isnull=False).order_by('-timestamp')[:200]:
            items.append({
                'url': m.document_url,
                'name': m.document_name or 'Document',
                'size': m.document_size or '',
                'id': m.id,
            })

    elif media_type == 'links':
        url_pattern = re.compile(r'https?://[^\s]+')
        for m in base_qs.filter(content__icontains='http').order_by('-timestamp')[:200]:
            urls = url_pattern.findall(m.content or '')
            for u in urls:
                items.append({
                    'url': u,
                    'text': (m.content or '')[:80],
                    'id': m.id,
                })

    return JsonResponse({'status': 'ok', 'items': items})


# ============================================================
# DISAPPEARING MESSAGES
# ============================================================

@csrf_exempt
@login_required
def set_disappearing(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    seconds = int(request.POST.get('seconds', 0))

    ChatSettings.objects.update_or_create(
        user=request.user, other_user=other,
        defaults={'disappearing_seconds': seconds}
    )
    ChatSettings.objects.update_or_create(
        user=other, other_user=request.user,
        defaults={'disappearing_seconds': seconds}
    )

    try:
        channel_layer = get_channel_layer()
        payload = {
            'type': 'disappearing_updated',
            'seconds': seconds,
            'user_id': request.user.id,
            'other_id': other.id,
        }
        async_to_sync(channel_layer.group_send)(f"user_{other.id}", payload)
        async_to_sync(channel_layer.group_send)(f"user_{request.user.id}", payload)
    except Exception as e:
        print(f"WS send error: {e}")

    return JsonResponse({'status': 'ok', 'seconds': seconds})


@login_required
def get_disappearing(request, user_id):
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    try:
        setting = ChatSettings.objects.get(user=request.user, other_user=other)
        seconds = setting.disappearing_seconds
    except ChatSettings.DoesNotExist:
        seconds = 0

    return JsonResponse({'status': 'ok', 'seconds': seconds})


# ============================================================
# FRIEND REQUESTS
# ============================================================

@csrf_exempt
@login_required
def send_friend_request(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    handle = request.POST.get('handle', '').strip().lower()
    if not handle:
        return JsonResponse({'status': 'fail', 'message': 'Handle required'})

    try:
        receiver = User.objects.get(profile__handle=handle)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    if receiver == request.user:
        return JsonResponse({'status': 'fail', 'message': 'Cannot send request to yourself'})

    if FriendRequest.objects.filter(
        Q(sender=request.user, receiver=receiver, status='accepted') |
        Q(sender=receiver, receiver=request.user, status='accepted')
    ).exists():
        return JsonResponse({'status': 'fail', 'message': 'Already friends'})

    if FriendRequest.objects.filter(sender=request.user, receiver=receiver, status='pending').exists():
        return JsonResponse({'status': 'fail', 'message': 'Request already sent'})

    FriendRequest.objects.update_or_create(
        sender=request.user, receiver=receiver,
        defaults={'status': 'pending'}
    )

    try:
        channel_layer = get_channel_layer()
        payload = {
            'type': 'new_friend_request_event',
            'from_user_id': request.user.id,
            'from_username': request.user.username,
            'from_avatar': _avatar_url(request.user),
        }
        async_to_sync(channel_layer.group_send)(f"user_{receiver.id}", payload)
    except Exception as e:
        print(f"WS send error: {e}")

    return JsonResponse({'status': 'ok', 'message': 'Request sent'})


@csrf_exempt
@login_required
def accept_friend_request(request, request_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        fr = FriendRequest.objects.get(id=request_id, receiver=request.user)
    except FriendRequest.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Request not found'})

    fr.status = 'accepted'
    fr.save()

    ChatContact.objects.get_or_create(user=request.user, contact=fr.sender)
    ChatContact.objects.get_or_create(user=fr.sender, contact=request.user)

    try:
        channel_layer = get_channel_layer()
        payload = {
            'type': 'friend_request_accepted_event',
            'by_user_id': request.user.id,
            'by_username': request.user.username,
            'avatar_url': _avatar_url(request.user),
        }
        async_to_sync(channel_layer.group_send)(f"user_{fr.sender.id}", payload)
    except Exception as e:
        print(f"WS send error: {e}")

    return JsonResponse({
        'status': 'ok',
        'message': 'Friend added',
        'sender_id': fr.sender.id,
        'sender_username': fr.sender.username,
        'sender_avatar': _avatar_url(fr.sender),
    })


@csrf_exempt
@login_required
def reject_friend_request(request, request_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        fr = FriendRequest.objects.get(id=request_id, receiver=request.user)
    except FriendRequest.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Request not found'})

    fr.status = 'rejected'
    fr.save()
    return JsonResponse({'status': 'ok', 'message': 'Request rejected'})


@login_required
def get_friend_requests(request):
    requests = FriendRequest.objects.filter(receiver=request.user, status='pending')
    data = []
    for r in requests:
        data.append({
            'id': r.id,
            'sender': r.sender.username,
            'sender_id': r.sender.id,
            'avatar_url': _avatar_url(r.sender),
        })
    return JsonResponse({'requests': data})


# ============================================================
# SEARCH
# ============================================================

@login_required
def search_handle(request):
    query = request.GET.get('handle', '').strip()
    if not query:
        return JsonResponse({'status': 'fail', 'message': 'Query required'})

    user = None
    q_lower = query.lower()

    if q_lower.startswith('@'):
        handle = q_lower[1:]
        user = User.objects.filter(profile__handle__iexact=handle).first()

    if user is None:
        cleaned = query.replace('-', '').replace(' ', '')
        if cleaned.isdigit() and len(cleaned) == 8:
            formatted = f"{cleaned[:4]}-{cleaned[4:]}"
            user = User.objects.filter(profile__chatflow_id=formatted).first()

    if user is None:
        cleaned = query.replace(' ', '').replace('-', '').replace('+', '')
        if cleaned.isdigit() and len(cleaned) >= 7:
            user = User.objects.filter(profile__phone=query).first() or \
                   User.objects.filter(profile__phone__endswith=cleaned).first()

    if user is None and not q_lower.startswith('@'):
        user = User.objects.filter(profile__handle__iexact=q_lower).first()

    if user is None:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    if user == request.user:
        return JsonResponse({'status': 'fail', 'message': 'That is you'})

    handle = ''
    try:
        handle = user.profile.handle or ''
    except Exception:
        pass

    is_friend = FriendRequest.objects.filter(
        Q(sender=request.user, receiver=user, status='accepted') |
        Q(sender=user, receiver=request.user, status='accepted')
    ).exists()

    is_pending = FriendRequest.objects.filter(
        Q(sender=request.user, receiver=user, status='pending') |
        Q(sender=user, receiver=request.user, status='pending')
    ).exists()

    return JsonResponse({
        'status': 'ok',
        'username': user.username,
        'user_id': user.id,
        'handle': handle,
        'chatflow_id': getattr(user.profile, 'chatflow_id', '') or '',
        'avatar_url': _avatar_url(user),
        'is_friend': is_friend,
        'is_pending': is_pending,
        'already_contact': is_friend,
    })


# ============================================================
# CONTACT
# ============================================================

@csrf_exempt
@login_required
def contact_user(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    user_id = request.POST.get('user_id')
    if not user_id:
        return JsonResponse({'status': 'fail', 'message': 'user_id required'})

    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    if other == request.user:
        return JsonResponse({'status': 'fail', 'message': 'Cannot contact yourself'})

    already_friend = FriendRequest.objects.filter(
        Q(sender=request.user, receiver=other, status='accepted') |
        Q(sender=other, receiver=request.user, status='accepted')
    ).exists()
    if already_friend:
        return JsonResponse({
            'status': 'ok',
            'already_friend': True,
            'message': 'Already friends',
            'user_id': other.id,
            'username': other.username,
            'avatar_url': _avatar_url(other),
        })

    existing = FriendRequest.objects.filter(
        sender=request.user, receiver=other
    ).first()
    if existing:
        if existing.status == 'pending':
            return JsonResponse({
                'status': 'ok',
                'already_sent': True,
                'message': 'Request already sent',
            })
        if existing.status == 'rejected':
            diff = (timezone.now() - existing.updated_at).total_seconds()
            if diff < 24 * 60 * 60:
                hours_left = int((24 * 60 * 60 - diff) // 3600) + 1
                return JsonResponse({
                    'status': 'fail',
                    'message': f'Request cooldown — try again in {hours_left}h',
                })
            existing.status = 'pending'
            existing.save()
            _notify_new_friend_request(request.user, other)
            return JsonResponse({
                'status': 'ok',
                'request_sent': True,
                'message': 'Request re-sent',
            })

    reverse = FriendRequest.objects.filter(
        sender=other, receiver=request.user, status='pending'
    ).first()
    if reverse:
        reverse.status = 'accepted'
        reverse.save()
        ChatContact.objects.get_or_create(user=request.user, contact=other)
        ChatContact.objects.get_or_create(user=other, contact=request.user)
        try:
            channel_layer = get_channel_layer()
            payload = {
                'type': 'friend_request_accepted_event',
                'by_user_id': request.user.id,
                'by_username': request.user.username,
                'avatar_url': _avatar_url(request.user),
            }
            async_to_sync(channel_layer.group_send)(f"user_{other.id}", payload)
        except Exception as e:
            print(f"WS send error: {e}")
        return JsonResponse({
            'status': 'ok',
            'auto_accepted': True,
            'message': 'Now friends',
            'user_id': other.id,
            'username': other.username,
            'avatar_url': _avatar_url(other),
        })

    FriendRequest.objects.filter(sender=request.user, receiver=other).delete()
    FriendRequest.objects.create(sender=request.user, receiver=other)

    _notify_new_friend_request(request.user, other)

    return JsonResponse({
        'status': 'ok',
        'request_sent': True,
        'message': 'Friend request sent',
        'user_id': other.id,
        'username': other.username,
        'avatar_url': _avatar_url(other),
    })


def _notify_new_friend_request(from_user, to_user):
    try:
        channel_layer = get_channel_layer()
        payload = {
            'type': 'new_friend_request_event',
            'from_user_id': from_user.id,
            'from_username': from_user.username,
            'from_avatar': _avatar_url(from_user),
        }
        async_to_sync(channel_layer.group_send)(f"user_{to_user.id}", payload)
    except Exception as e:
        print(f"WS send error: {e}")


# ============================================================
# FILE UPLOADS
# ============================================================

def _broadcast_new_message(m, sender, receiver, caption=''):
    try:
        now = timezone.localtime(m.timestamp)
        today = timezone.localtime(timezone.now()).date()
        msg_date = now.date()
        if msg_date == today:
            date_display = 'TODAY'
        elif msg_date == today - timedelta(days=1):
            date_display = 'YESTERDAY'
        else:
            date_display = now.strftime('%d-%m-%Y')

        channel_layer = get_channel_layer()
        payload = {
            "type": "chat_message",
            "message": {
                "id": m.id,
                "sender": sender.username,
                "sender_id": sender.id,
                "receiver_id": receiver.id,
                "content": m.content or '',
                "caption": caption,
                "timestamp": _format_wa_time(now),
                "date_iso": msg_date.strftime('%Y-%m-%d'),
                "date_display": date_display,
                "image_url": m.image_url,
                "audio_url": m.audio_url,
                "audio_duration": m.audio_duration,
                "video_url": m.video_url,
                "video_duration": m.video_duration,
                "document_url": m.document_url,
                "document_name": m.document_name,
                "document_size": m.document_size,
                "reply_to": None,
                "forwarded": False,
                "is_read": False,
                "expires_at": m.expires_at.isoformat() if m.expires_at else None,
                "message_type": "text",
                "call_duration": "",
                "is_edited": False,
                "edited_at": "",
                "is_pinned": getattr(m, 'is_pinned', False),
            }
        }
        async_to_sync(channel_layer.group_send)(f"user_{receiver.id}", payload)
        async_to_sync(channel_layer.group_send)(f"user_{sender.id}", payload)
    except Exception as e:
        print(f"WS send error: {e}")


@csrf_exempt
@login_required
def upload_image(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    image = request.FILES.get('image')
    receiver_id = request.POST.get('receiver_id')
    caption = (request.POST.get('caption') or '').strip()

    if not image or not receiver_id:
        return JsonResponse({'status': 'fail', 'message': 'Missing data'})

    if image.size > IMAGE_MAX_BYTES:
        return JsonResponse({'status': 'fail', 'message': 'Image too large (max 100 MB)'})

    try:
        receiver = User.objects.get(id=receiver_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    ext = os.path.splitext(image.name)[1].lower() or '.jpg'
    filename = f"chat/images/{uuid.uuid4().hex}{ext}"
    saved_path = default_storage.save(filename, ContentFile(image.read()))
    image_url = settings.MEDIA_URL + saved_path

    expires_at = None
    try:
        cs = ChatSettings.objects.get(user=request.user, other_user=receiver)
        if cs.disappearing_seconds > 0:
            expires_at = timezone.now() + timedelta(seconds=cs.disappearing_seconds)
    except ChatSettings.DoesNotExist:
        pass

    m = Message.objects.create(
        sender=request.user, receiver=receiver,
        content=caption,
        image_url=image_url, expires_at=expires_at,
    )
    _broadcast_new_message(m, request.user, receiver, caption)
    now = timezone.localtime(m.timestamp)
    today = timezone.localtime(timezone.now()).date()
    msg_date = now.date()
    date_display = 'TODAY' if msg_date == today else ('YESTERDAY' if msg_date == today - timedelta(days=1) else now.strftime('%d-%m-%Y'))

    return JsonResponse({
        'status': 'ok', 'message_id': m.id, 'image_url': image_url,
        'timestamp': _format_wa_time(now),
        'date_iso': msg_date.strftime('%Y-%m-%d'),
        'date_display': date_display,
        'expires_at': expires_at.isoformat() if expires_at else None,
    })


@csrf_exempt
@login_required
def upload_audio(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    audio = request.FILES.get('audio')
    receiver_id = request.POST.get('receiver_id')
    duration = request.POST.get('duration', '0:00')
    if not audio or not receiver_id:
        return JsonResponse({'status': 'fail', 'message': 'Missing data'})

    if audio.size > AUDIO_MAX_BYTES:
        return JsonResponse({'status': 'fail', 'message': 'Audio too large (max 16 MB)'})

    try:
        receiver = User.objects.get(id=receiver_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    ext = os.path.splitext(audio.name)[1].lower() or '.webm'
    filename = f"chat/audio/{uuid.uuid4().hex}{ext}"
    saved_path = default_storage.save(filename, ContentFile(audio.read()))
    audio_url = settings.MEDIA_URL + saved_path

    expires_at = None
    try:
        cs = ChatSettings.objects.get(user=request.user, other_user=receiver)
        if cs.disappearing_seconds > 0:
            expires_at = timezone.now() + timedelta(seconds=cs.disappearing_seconds)
    except ChatSettings.DoesNotExist:
        pass

    m = Message.objects.create(
        sender=request.user, receiver=receiver, content='',
        audio_url=audio_url, audio_duration=duration, expires_at=expires_at,
    )
    _broadcast_new_message(m, request.user, receiver)
    now = timezone.localtime(m.timestamp)
    today = timezone.localtime(timezone.now()).date()
    msg_date = now.date()
    date_display = 'TODAY' if msg_date == today else ('YESTERDAY' if msg_date == today - timedelta(days=1) else now.strftime('%d-%m-%Y'))

    return JsonResponse({
        'status': 'ok', 'message_id': m.id, 'audio_url': audio_url,
        'audio_duration': duration,
        'timestamp': _format_wa_time(now),
        'date_iso': msg_date.strftime('%Y-%m-%d'),
        'date_display': date_display,
        'expires_at': expires_at.isoformat() if expires_at else None,
    })


@csrf_exempt
@login_required
def upload_video(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    video = request.FILES.get('video')
    receiver_id = request.POST.get('receiver_id')
    duration = request.POST.get('duration', '0:00')
    caption = (request.POST.get('caption') or '').strip()

    if not video or not receiver_id:
        return JsonResponse({'status': 'fail', 'message': 'Missing data'})

    if video.size > VIDEO_MAX_BYTES:
        return JsonResponse({'status': 'fail', 'message': 'Video too large (max 100 MB)'})

    try:
        receiver = User.objects.get(id=receiver_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    ext = os.path.splitext(video.name)[1].lower() or '.mp4'
    filename = f"chat/video/{uuid.uuid4().hex}{ext}"
    saved_path = default_storage.save(filename, ContentFile(video.read()))
    video_url = settings.MEDIA_URL + saved_path

    expires_at = None
    try:
        cs = ChatSettings.objects.get(user=request.user, other_user=receiver)
        if cs.disappearing_seconds > 0:
            expires_at = timezone.now() + timedelta(seconds=cs.disappearing_seconds)
    except ChatSettings.DoesNotExist:
        pass

    m = Message.objects.create(
        sender=request.user, receiver=receiver,
        content=caption,
        video_url=video_url, video_duration=duration, expires_at=expires_at,
    )
    _broadcast_new_message(m, request.user, receiver, caption)
    now = timezone.localtime(m.timestamp)
    today = timezone.localtime(timezone.now()).date()
    msg_date = now.date()
    date_display = 'TODAY' if msg_date == today else ('YESTERDAY' if msg_date == today - timedelta(days=1) else now.strftime('%d-%m-%Y'))

    return JsonResponse({
        'status': 'ok', 'message_id': m.id, 'video_url': video_url,
        'video_duration': duration,
        'timestamp': _format_wa_time(now),
        'date_iso': msg_date.strftime('%Y-%m-%d'),
        'date_display': date_display,
        'expires_at': expires_at.isoformat() if expires_at else None,
    })


@csrf_exempt
@login_required
def upload_document(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    document = request.FILES.get('document')
    receiver_id = request.POST.get('receiver_id')
    if not document or not receiver_id:
        return JsonResponse({'status': 'fail', 'message': 'Missing data'})

    if document.size > DOCUMENT_MAX_BYTES:
        return JsonResponse({'status': 'fail', 'message': 'Document too large (max 100 MB)'})

    try:
        receiver = User.objects.get(id=receiver_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    ext = os.path.splitext(document.name)[1].lower()
    filename = f"chat/documents/{uuid.uuid4().hex}{ext}"
    saved_path = default_storage.save(filename, ContentFile(document.read()))
    document_url = settings.MEDIA_URL + saved_path

    file_size = document.size
    if file_size < 1024:
        size_str = f"{file_size} B"
    elif file_size < 1024 * 1024:
        size_str = f"{file_size / 1024:.1f} KB"
    else:
        size_str = f"{file_size / (1024 * 1024):.1f} MB"

    expires_at = None
    try:
        cs = ChatSettings.objects.get(user=request.user, other_user=receiver)
        if cs.disappearing_seconds > 0:
            expires_at = timezone.now() + timedelta(seconds=cs.disappearing_seconds)
    except ChatSettings.DoesNotExist:
        pass

    m = Message.objects.create(
        sender=request.user, receiver=receiver, content='',
        document_url=document_url, document_name=document.name,
        document_size=size_str, expires_at=expires_at,
    )
    _broadcast_new_message(m, request.user, receiver)
    now = timezone.localtime(m.timestamp)
    today = timezone.localtime(timezone.now()).date()
    msg_date = now.date()
    date_display = 'TODAY' if msg_date == today else ('YESTERDAY' if msg_date == today - timedelta(days=1) else now.strftime('%d-%m-%Y'))

    return JsonResponse({
        'status': 'ok', 'message_id': m.id,
        'document_url': document_url, 'document_name': document.name,
        'document_size': size_str,
        'timestamp': _format_wa_time(now),
        'date_iso': msg_date.strftime('%Y-%m-%d'),
        'date_display': date_display,
        'expires_at': expires_at.isoformat() if expires_at else None,
    })


# ============================================================
# PROFILE
# ============================================================

@login_required
def profile_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not profile.chatflow_id:
        profile.chatflow_id = _generate_chatflow_id()
        profile.save(update_fields=['chatflow_id'])

    sidebar = _get_sidebar_users(request.user)

    chat_users = []
    for u in sidebar:
        try:
            cs = ChatSettings.objects.get(user=request.user, other_user_id=u['id'])
            disappear_seconds = cs.disappearing_seconds
        except ChatSettings.DoesNotExist:
            disappear_seconds = 0
        chat_users.append({
            'id': u['id'],
            'username': u['username'],
            'avatar_url': u['avatar_url'],
            'last_ts': u['last_ts'],
            'disappearing_seconds': disappear_seconds,
        })

    return render(request, 'chat/profile.html', {
        'current_user': request.user,
        'profile': profile,
        'chat_users': chat_users,
    })


@login_required
def user_profile_view(request, user_id):
    other_user = get_object_or_404(User, id=user_id)
    if other_user == request.user:
        return redirect('profile')
    try:
        profile = UserProfile.objects.get(user=other_user)
    except UserProfile.DoesNotExist:
        profile = None
    messages_sent = Message.objects.filter(sender=other_user).count()
    messages_received = Message.objects.filter(receiver=other_user).count()
    total_chats = Message.objects.filter(
        Q(sender=other_user) | Q(receiver=other_user)
    ).values('sender', 'receiver').distinct().count()
    is_online = other_user.id in ONLINE_USERS
    return render(request, 'chat/user_profile.html', {
        'profile_user': other_user,
        'profile': profile,
        'is_online': is_online,
        'messages_sent': messages_sent,
        'messages_received': messages_received,
        'total_chats': total_chats,
    })


@csrf_exempt
@login_required
def update_profile(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    bio = request.POST.get('bio', '').strip()
    phone = request.POST.get('phone', '').strip()
    profile.bio = bio
    profile.phone = phone
    profile.save()
    return JsonResponse({'status': 'ok'})


@csrf_exempt
@login_required
def update_handle(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    handle = request.POST.get('handle', '').strip().lower()
    if not handle:
        return JsonResponse({'status': 'fail', 'message': 'Handle required'})
    if UserProfile.objects.filter(handle=handle).exclude(user=request.user).exists():
        return JsonResponse({'status': 'fail', 'message': 'Handle already taken'})

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile.handle = handle
    profile.save()
    return JsonResponse({'status': 'ok'})


@csrf_exempt
@login_required
def upload_avatar(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    avatar = request.FILES.get('avatar')
    if not avatar:
        return JsonResponse({'status': 'fail', 'message': 'No file'})
    if not avatar.content_type.startswith('image/'):
        return JsonResponse({'status': 'fail', 'message': 'Only images allowed'})
    if avatar.size > 5 * 1024 * 1024:
        return JsonResponse({'status': 'fail', 'message': 'Max 5MB'})

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if profile.avatar:
        try:
            profile.avatar.delete(save=False)
        except Exception:
            pass
    profile.avatar = avatar
    profile.save()
    return JsonResponse({'status': 'ok', 'avatar_url': profile.avatar.url})


@csrf_exempt
@login_required
def delete_avatar(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    try:
        profile = UserProfile.objects.get(user=request.user)
    except UserProfile.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Profile not found'})
    if not profile.avatar:
        return JsonResponse({'status': 'fail', 'message': 'No avatar to delete'})
    try:
        profile.avatar.delete(save=False)
    except Exception as e:
        print(f"Avatar delete error: {e}")
    profile.avatar = None
    profile.save()
    return JsonResponse({'status': 'ok', 'message': 'Avatar deleted'})


@csrf_exempt
@login_required
def change_password(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    old_password = request.POST.get('old_password', '')
    new_password = request.POST.get('new_password', '')
    confirm_password = request.POST.get('confirm_password', '')

    if not old_password or not new_password:
        return JsonResponse({'status': 'fail', 'message': 'All fields required'})
    if new_password != confirm_password:
        return JsonResponse({'status': 'fail', 'message': 'Passwords do not match'})
    if len(new_password) < 6:
        return JsonResponse({'status': 'fail', 'message': 'Min 6 characters'})
    if not request.user.check_password(old_password):
        return JsonResponse({'status': 'fail', 'message': 'Old password wrong'})

    request.user.set_password(new_password)
    request.user.save()
    login(request, request.user)
    return JsonResponse({'status': 'ok'})


@csrf_exempt
@login_required
def update_email(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        request.user.email = email
        request.user.save()
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'status': 'fail'})


# ============================================================
# CALLS
# ============================================================

@login_required
def call_history(request, user_id):
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    calls = Call.objects.filter(
        Q(caller=request.user, receiver=other) |
        Q(caller=other, receiver=request.user)
    ).order_by('-started_at')[:50]

    data = []
    for c in calls:
        data.append({
            'id': c.id,
            'caller': c.caller.username,
            'receiver': c.receiver.username,
            'call_type': c.call_type,
            'status': c.status,
            'duration': c.duration,
            'started_at': _format_wa_time(timezone.localtime(c.started_at)),
            'date_iso': timezone.localtime(c.started_at).strftime('%Y-%m-%d'),
        })
    return JsonResponse({'status': 'ok', 'calls': data})


# ============================================================
# SETTINGS
# ============================================================

@csrf_exempt
@login_required
def toggle_read_receipts(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    enabled = request.POST.get('enabled', '1') in ('1', 'true', 'True', 'yes')
    profile.show_read_receipts = enabled
    profile.save(update_fields=['show_read_receipts'])
    return JsonResponse({'status': 'ok', 'show_read_receipts': enabled})


@login_required
def get_read_receipts(request):
    try:
        profile = UserProfile.objects.get(user=request.user)
        enabled = profile.show_read_receipts
    except UserProfile.DoesNotExist:
        enabled = True
    return JsonResponse({'status': 'ok', 'show_read_receipts': enabled})


@csrf_exempt
@login_required
def set_font_size(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    size = request.POST.get('size', 'medium')
    if size not in ('small', 'medium', 'large'):
        return JsonResponse({'status': 'fail', 'message': 'Invalid size'})
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile.font_size = size
    profile.save(update_fields=['font_size'])
    return JsonResponse({'status': 'ok', 'font_size': size})


@login_required
def get_font_size(request):
    try:
        profile = UserProfile.objects.get(user=request.user)
        size = profile.font_size or 'medium'
    except UserProfile.DoesNotExist:
        size = 'medium'
    return JsonResponse({'status': 'ok', 'font_size': size})


@csrf_exempt
@login_required
def toggle_sound(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    contact, _ = ChatContact.objects.get_or_create(user=request.user, contact=other)
    contact.sound_enabled = not contact.sound_enabled
    contact.save(update_fields=['sound_enabled'])

    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(f"user_{request.user.id}", {
            'type': 'contact_updated',
            'contact_id': other.id,
            'sound_enabled': contact.sound_enabled,
        })
    except Exception as e:
        print(f"WS send error: {e}")

    return JsonResponse({'status': 'ok', 'sound_enabled': contact.sound_enabled})


@csrf_exempt
@login_required
def set_wallpaper(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    wallpaper = request.POST.get('wallpaper', '').strip()[:255]

    ChatSettings.objects.update_or_create(
        user=request.user, other_user=other,
        defaults={'wallpaper': wallpaper},
    )

    try:
        channel_layer = get_channel_layer()
        payload = {
            'type': 'wallpaper_updated',
            'user_id': request.user.id,
            'other_id': other.id,
            'wallpaper': wallpaper,
        }
        async_to_sync(channel_layer.group_send)(f"user_{request.user.id}", payload)
    except Exception as e:
        print(f"WS send error: {e}")

    return JsonResponse({'status': 'ok', 'wallpaper': wallpaper})


@login_required
def get_wallpaper(request, user_id):
    try:
        other = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not found'})

    try:
        setting = ChatSettings.objects.get(user=request.user, other_user=other)
        wallpaper = setting.wallpaper or ''
    except ChatSettings.DoesNotExist:
        wallpaper = ''

    return JsonResponse({'status': 'ok', 'wallpaper': wallpaper})


# ============================================================
# ⭐ AI VOICE ENDPOINTS (Sarvam)
# ============================================================

@csrf_exempt
@login_required
def ai_speech_to_text(request):
    """Sarvam Speech-to-Text: audio → text."""
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'status': 'fail', 'message': 'No audio provided'})

    api_key = getattr(settings, 'SARVAM_API_KEY', '')
    if not api_key:
        return JsonResponse({'status': 'fail', 'message': 'Sarvam API key not configured'})

    try:
        url = "https://api.sarvam.ai/speech-to-text"
        headers = {"api-subscription-key": api_key}
        files = {"file": ('voice.wav', audio_file.read(), 'audio/wav')}
        data = {
            "language_code": getattr(settings, 'SARVAM_STT_LANGUAGE', 'ta-IN'),
            "model": "saarika:v2",
        }

        resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)

        if resp.status_code == 200:
            result = resp.json()
            text = result.get('transcript', '') or result.get('text', '')
            if not text:
                return JsonResponse({'status': 'fail', 'message': 'No transcript returned'})
            return JsonResponse({'status': 'ok', 'text': text})
        else:
            return JsonResponse({
                'status': 'fail',
                'message': f'Sarvam STT error {resp.status_code}',
                'detail': resp.text[:300],
            })
    except Exception as e:
        print(f"[SARVAM STT ERROR] {e}")
        return JsonResponse({'status': 'fail', 'message': f'STT error: {str(e)}'})


@csrf_exempt
@login_required
def ai_text_to_speech(request):
    """Sarvam Text-to-Speech: text → audio (base64)."""
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    text = (request.POST.get('text') or '').strip()
    if not text:
        return JsonResponse({'status': 'fail', 'message': 'No text provided'})

    api_key = getattr(settings, 'SARVAM_API_KEY', '')
    if not api_key:
        return JsonResponse({'status': 'fail', 'message': 'Sarvam API key not configured'})

    try:
        url = "https://api.sarvam.ai/text-to-speech"
        headers = {
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": [text[:500]],
            "target_language_code": getattr(settings, 'SARVAM_TTS_LANGUAGE', 'ta-IN'),
            "speaker": getattr(settings, 'SARVAM_TTS_SPEAKER', 'anushka'),
            "speech_sample_rate": 8000,
            "enable_preprocessing": True,
            "model": "bulbul:v1",
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=60)

        if resp.status_code == 200:
            result = resp.json()
            audios = result.get('audios', [])
            if audios:
                return JsonResponse({
                    'status': 'ok',
                    'audio_base64': audios[0],
                    'format': 'wav',
                })
            return JsonResponse({'status': 'fail', 'message': 'No audio returned'})
        else:
            return JsonResponse({
                'status': 'fail',
                'message': f'Sarvam TTS error {resp.status_code}',
                'detail': resp.text[:300],
            })
    except Exception as e:
        print(f"[SARVAM TTS ERROR] {e}")
        return JsonResponse({'status': 'fail', 'message': f'TTS error: {str(e)}'})


@csrf_exempt
@login_required
def ai_chat(request):
    """AI chat fallback (non-WebSocket)."""
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    message = (request.POST.get('message') or '').strip()
    if not message:
        return JsonResponse({'status': 'fail', 'message': 'No message'})

    try:
        ai_user = User.objects.get(username=AI_USERNAME)
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'AI not found'})

    # Get history
    history = []
    msgs = Message.objects.filter(
        Q(sender=request.user, receiver=ai_user) | Q(sender=ai_user, receiver=request.user)
    ).order_by('-timestamp')[:6]
    for m in reversed(list(msgs)):
        history.append({
            'role': 'user' if m.sender_id == request.user.id else 'assistant',
            'content': m.content or '',
        })

    # Save user message
    user_msg = Message.objects.create(
        sender=request.user, receiver=ai_user,
        content=message, message_type='text',
    )

    # Get AI reply
    ai_text = _ask_ai(message, history)

    # Save AI message
    ai_msg = Message.objects.create(
        sender=ai_user, receiver=request.user,
        content=ai_text, message_type='text',
    )

    # Broadcast via WebSocket
    try:
        channel_layer = get_channel_layer()
        now = timezone.localtime(ai_msg.timestamp)
        today = timezone.localtime(timezone.now()).date()
        msg_date = now.date()
        if msg_date == today:
            date_display = 'TODAY'
        elif msg_date == today - timedelta(days=1):
            date_display = 'YESTERDAY'
        else:
            date_display = now.strftime('%d-%m-%Y')

        payload = {
            "type": "chat_message",
            "message": {
                "id": ai_msg.id,
                "sender": ai_user.username,
                "sender_id": ai_user.id,
                "receiver_id": request.user.id,
                "content": ai_text,
                "timestamp": _format_wa_time(now),
                "date_iso": msg_date.strftime('%Y-%m-%d'),
                "date_display": date_display,
                "is_read": False,
                "message_type": "text",
                "is_edited": False,
                "edited_at": "",
            }
        }
        async_to_sync(channel_layer.group_send)(f"user_{request.user.id}", payload)
    except Exception as e:
        print(f"[AI BROADCAST ERROR] {e}")

    return JsonResponse({
        'status': 'ok',
        'user_message': {'id': user_msg.id, 'content': message},
        'ai_reply': {'id': ai_msg.id, 'content': ai_text},
    })


# ============================================================
# STATUS (delegated)
# ============================================================

@login_required
def status_list_view(request):
    from . import status_views
    return status_views.list_statuses(request)


@csrf_exempt
@login_required
def status_create_view(request):
    from . import status_views
    return status_views.create_status(request)


@csrf_exempt
@login_required
def status_view_view(request, status_id):
    from . import status_views
    return status_views.view_status(request, status_id)


@login_required
def status_viewers_view(request, status_id):
    from . import status_views
    return status_views.get_status_viewers(request, status_id)


@csrf_exempt
@login_required
def status_delete_view(request, status_id):
    from . import status_views
    return status_views.delete_status(request, status_id)


@csrf_exempt
@login_required
def status_reply_view(request, status_id):
    from . import status_views
    return status_views.reply_to_status(request, status_id)


@login_required
def status_info_view(request, status_id):
    from . import status_views
    return status_views.get_status_info(request, status_id)
# ============================================================
# ⭐ AI VOICE ENDPOINTS (Sarvam)
# ============================================================

@csrf_exempt
@login_required
def ai_speech_to_text(request):
    """Sarvam Speech-to-Text: audio → text."""
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    audio_file = request.FILES.get('audio')
    if not audio_file:
        return JsonResponse({'status': 'fail', 'message': 'No audio provided'})

    api_key = getattr(settings, 'SARVAM_API_KEY', '')
    if not api_key:
        return JsonResponse({'status': 'fail', 'message': 'Sarvam API key not configured'})

    try:
        url = "https://api.sarvam.ai/speech-to-text"
        headers = {"api-subscription-key": api_key}
        files = {"file": ('voice.wav', audio_file.read(), 'audio/wav')}
        data = {
            "language_code": getattr(settings, 'SARVAM_STT_LANGUAGE', 'ta-IN'),
            "model": "saarika:v2",
        }

        resp = requests.post(url, headers=headers, files=files, data=data, timeout=60)

        if resp.status_code == 200:
            result = resp.json()
            text = result.get('transcript', '') or result.get('text', '')
            if not text:
                return JsonResponse({'status': 'fail', 'message': 'No transcript returned'})
            return JsonResponse({'status': 'ok', 'text': text})
        else:
            return JsonResponse({
                'status': 'fail',
                'message': f'Sarvam STT error {resp.status_code}',
                'detail': resp.text[:300],
            })
    except Exception as e:
        print(f"[SARVAM STT ERROR] {e}")
        return JsonResponse({'status': 'fail', 'message': f'STT error: {str(e)}'})


@csrf_exempt
@login_required
def ai_text_to_speech(request):
    """Sarvam Text-to-Speech: text → audio (base64)."""
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    text = (request.POST.get('text') or '').strip()
    if not text:
        return JsonResponse({'status': 'fail', 'message': 'No text provided'})

    api_key = getattr(settings, 'SARVAM_API_KEY', '')
    if not api_key:
        return JsonResponse({'status': 'fail', 'message': 'Sarvam API key not configured'})

    try:
        url = "https://api.sarvam.ai/text-to-speech"
        headers = {
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": [text[:500]],
            "target_language_code": getattr(settings, 'SARVAM_TTS_LANGUAGE', 'ta-IN'),
            "speaker": getattr(settings, 'SARVAM_TTS_SPEAKER', 'anushka'),
            "speech_sample_rate": 8000,
            "enable_preprocessing": True,
            "model": "bulbul:v1",
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=60)

        if resp.status_code == 200:
            result = resp.json()
            audios = result.get('audios', [])
            if audios:
                return JsonResponse({
                    'status': 'ok',
                    'audio_base64': audios[0],
                    'format': 'wav',
                })
            return JsonResponse({'status': 'fail', 'message': 'No audio returned'})
        else:
            return JsonResponse({
                'status': 'fail',
                'message': f'Sarvam TTS error {resp.status_code}',
                'detail': resp.text[:300],
            })
    except Exception as e:
        print(f"[SARVAM TTS ERROR] {e}")
        return JsonResponse({'status': 'fail', 'message': f'TTS error: {str(e)}'})


@csrf_exempt
@login_required
def ai_chat(request):
    """AI chat fallback (non-WebSocket)."""
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    message = (request.POST.get('message') or '').strip()
    if not message:
        return JsonResponse({'status': 'fail', 'message': 'No message'})

    try:
        ai_user = User.objects.get(username='ChatFlow AI')
    except User.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'AI not found'})

    history = []
    msgs = Message.objects.filter(
        Q(sender=request.user, receiver=ai_user) | Q(sender=ai_user, receiver=request.user)
    ).order_by('-timestamp')[:6]
    for m in reversed(list(msgs)):
        history.append({
            'role': 'user' if m.sender_id == request.user.id else 'assistant',
            'content': m.content or '',
        })

    user_msg = Message.objects.create(
        sender=request.user, receiver=ai_user,
        content=message, message_type='text',
    )

    ai_text = _ask_ai(message, history)

    ai_msg = Message.objects.create(
        sender=ai_user, receiver=request.user,
        content=ai_text, message_type='text',
    )

    try:
        channel_layer = get_channel_layer()
        now = timezone.localtime(ai_msg.timestamp)
        today = timezone.localtime(timezone.now()).date()
        msg_date = now.date()
        if msg_date == today:
            date_display = 'TODAY'
        elif msg_date == today - timedelta(days=1):
            date_display = 'YESTERDAY'
        else:
            date_display = now.strftime('%d-%m-%Y')

        payload = {
            "type": "chat_message",
            "message": {
                "id": ai_msg.id,
                "sender": ai_user.username,
                "sender_id": ai_user.id,
                "receiver_id": request.user.id,
                "content": ai_text,
                "timestamp": _format_wa_time(now),
                "date_iso": msg_date.strftime('%Y-%m-%d'),
                "date_display": date_display,
                "is_read": False,
                "message_type": "text",
                "is_edited": False,
                "edited_at": "",
            }
        }
        async_to_sync(channel_layer.group_send)(f"user_{request.user.id}", payload)
    except Exception as e:
        print(f"[AI BROADCAST ERROR] {e}")

    return JsonResponse({
        'status': 'ok',
        'user_message': {'id': user_msg.id, 'content': message},
        'ai_reply': {'id': ai_msg.id, 'content': ai_text},
    })


# ============================================================
# END OF FILE
# ============================================================