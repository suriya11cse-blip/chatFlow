import os
import uuid
import random
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


def _avatar_url(user):
    try:
        p = UserProfile.objects.get(user=user)
        if p.avatar:
            return p.avatar.url
    except UserProfile.DoesNotExist:
        pass
    return ''


def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)
        if user:
            login(request, user)
            return redirect('home')
        return render(request, 'chat/login.html', {'error': 'Invalid credentials'})
    return render(request, 'chat/login.html')


def _generate_chatflow_id():
    """Generate a unique 8-digit ID like '8435-2234'."""
    for _ in range(50):
        part1 = str(random.randint(1000, 9999))
        part2 = str(random.randint(1000, 9999))
        candidate = f"{part1}-{part2}"
        if not UserProfile.objects.filter(chatflow_id=candidate).exists():
            return candidate
    return f"{random.randint(10000000, 99999999)}"


def register_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        if User.objects.filter(username=u).exists():
            return render(request, 'chat/register.html', {'error': 'Username already exists'})
        user = User.objects.create_user(username=u, password=p)
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.chatflow_id:
            profile.chatflow_id = _generate_chatflow_id()
            profile.save(update_fields=['chatflow_id'])
        return redirect('login')
    return render(request, 'chat/register.html')


def logout_view(request):
    logout(request)
    return redirect('login')


def _last_msg_preview(m):
    if not m:
        return ''
    if m.deleted_for_everyone:
        return 'Message deleted'
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
    """
    Privacy-first: Show only ACCEPTED friends in sidebar.
    ChatContact entries are ignored unless there's an accepted friend request.
    """
    # Accepted friend ids (either direction)
    pairs = FriendRequest.objects.filter(
        Q(sender=user, status='accepted') | Q(receiver=user, status='accepted')
    ).values_list('sender_id', 'receiver_id')

    friend_set = set()
    for s, r in pairs:
        friend_set.add(r if s == user.id else s)
    friend_set.discard(user.id)

    users = list(User.objects.filter(id__in=friend_set))

    results = []
    for u in users:
        last_msg = Message.objects.filter(
            Q(sender=user, receiver=u) | Q(sender=u, receiver=user)
        ).exclude(deleted_for=user).order_by('-timestamp').first()

        unread = Message.objects.filter(
            sender=u, receiver=user, is_read=False
        ).exclude(deleted_for_everyone=True).exclude(deleted_for=user).count()

        results.append({
            'id': u.id,
            'username': u.username,
            'avatar_url': _avatar_url(u),
            'last_msg': _last_msg_preview(last_msg),
            'unread': unread,
            'last_ts': last_msg.timestamp if last_msg else None,
        })

    with_msg = [r for r in results if r['last_ts'] is not None]
    without_msg = [r for r in results if r['last_ts'] is None]
    with_msg.sort(key=lambda x: x['last_ts'], reverse=True)
    without_msg.sort(key=lambda x: x['username'].lower())

    return with_msg + without_msg


@login_required
def home(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not profile.chatflow_id:
        profile.chatflow_id = _generate_chatflow_id()
        profile.save(update_fields=['chatflow_id'])

    sidebar = _get_sidebar_users(request.user)

    users_data_final = []
    for ud in sidebar:
        users_data_final.append({
            'user': {'id': ud['id'], 'username': ud['username']},
            'avatar_url': ud['avatar_url'],
            'last_content': ud['last_msg'],
            'unread_count': ud['unread'],
        })

    my_avatar_url = profile.avatar.url if profile.avatar else ''
    online_user_ids = list(ONLINE_USERS)

    return render(request, 'chat/chat.html', {
        'users_data': users_data_final,
        'current_user': request.user,
        'my_avatar_url': my_avatar_url,
        'online_user_ids': online_user_ids,
    })


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
    ).exclude(deleted_for=request.user).order_by('timestamp')

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
            'receiver': m.receiver.username,
            'receiver_id': m.receiver.id,
            'content': '' if m.deleted_for_everyone else (m.content or ''),
            'timestamp': now_local.strftime('%I:%M %p'),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
            'deleted_for_everyone': m.deleted_for_everyone,
            'is_mine': m.sender == request.user,
            'is_read': m.is_read,
            'is_starred': m.is_starred,
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
            'edited_at': timezone.localtime(m.edited_at).strftime('%I:%M %p') if getattr(m, 'edited_at', None) else '',
        })

    return JsonResponse({'messages': data})


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


# ============ DISAPPEARING MESSAGES ============

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


# ============ FRIEND REQUESTS (privacy-first) ============

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

    # Notify receiver realtime
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

    # Create ChatContact both directions
    ChatContact.objects.get_or_create(user=request.user, contact=fr.sender)
    ChatContact.objects.get_or_create(user=fr.sender, contact=request.user)

    # Notify sender via WS (with avatar)
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


# ============ SEARCH ============

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
        'already_contact': is_friend,  # for UI backwards compat
    })


# ============ CONTACT (privacy-first → friend request) ============

@csrf_exempt
@login_required
def contact_user(request):
    """
    Privacy-first: Instead of directly opening chat, sends a friend request.
    Chat opens only after the receiver accepts.
    """
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

    # ── Already friends?
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

    # ── Existing request from me → other
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

    # ── Reverse request from other → me (mutual → auto-accept)
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

    # ── Fresh request (delete leftover to avoid unique constraint)
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
    """Notify receiver real-time that a new friend request arrived."""
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


# ============ UPLOAD ============

@csrf_exempt
@login_required
def upload_image(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})
    image = request.FILES.get('image')
    receiver_id = request.POST.get('receiver_id')
    if not image or not receiver_id:
        return JsonResponse({'status': 'fail', 'message': 'Missing data'})
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
        sender=request.user, receiver=receiver, content='',
        image_url=image_url, expires_at=expires_at,
    )
    _broadcast_new_message(m, request.user, receiver)
    now = timezone.localtime(m.timestamp)
    today = timezone.localtime(timezone.now()).date()
    msg_date = now.date()
    date_display = 'TODAY' if msg_date == today else ('YESTERDAY' if msg_date == today - timedelta(days=1) else now.strftime('%d-%m-%Y'))

    return JsonResponse({
        'status': 'ok', 'message_id': m.id, 'image_url': image_url,
        'timestamp': now.strftime('%I:%M %p'),
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
        'timestamp': now.strftime('%I:%M %p'),
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
    if not video or not receiver_id:
        return JsonResponse({'status': 'fail', 'message': 'Missing data'})
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
        sender=request.user, receiver=receiver, content='',
        video_url=video_url, video_duration=duration, expires_at=expires_at,
    )
    _broadcast_new_message(m, request.user, receiver)
    now = timezone.localtime(m.timestamp)
    today = timezone.localtime(timezone.now()).date()
    msg_date = now.date()
    date_display = 'TODAY' if msg_date == today else ('YESTERDAY' if msg_date == today - timedelta(days=1) else now.strftime('%d-%m-%Y'))

    return JsonResponse({
        'status': 'ok', 'message_id': m.id, 'video_url': video_url,
        'video_duration': duration,
        'timestamp': now.strftime('%I:%M %p'),
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
        'timestamp': now.strftime('%I:%M %p'),
        'date_iso': msg_date.strftime('%Y-%m-%d'),
        'date_display': date_display,
        'expires_at': expires_at.isoformat() if expires_at else None,
    })


def _broadcast_new_message(m, sender, receiver):
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
                "timestamp": now.strftime('%I:%M %p'),
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
            }
        }
        async_to_sync(channel_layer.group_send)(f"user_{receiver.id}", payload)
        async_to_sync(channel_layer.group_send)(f"user_{sender.id}", payload)
    except Exception as e:
        print(f"WS send error: {e}")


# ============ PROFILE ============

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


# ============ CALL HISTORY ============

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
            'started_at': timezone.localtime(c.started_at).strftime('%I:%M %p'),
            'date_iso': timezone.localtime(c.started_at).strftime('%Y-%m-%d'),
        })
    return JsonResponse({'status': 'ok', 'calls': data})