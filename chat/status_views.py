# chat/status_views.py
"""
Status / Stories feature — WhatsApp style.
- Text, Image, Video status
- 24-hour auto-expire
- View tracking
- Reply to status
"""
import os
import uuid
from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db.models import Q, Count, Max
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import Status, StatusView, FriendRequest, ChatContact


STATUS_EXPIRY_HOURS = 24


# ============================================================
# HELPERS
# ============================================================

def _status_media_url(status):
    """Return media_url for status."""
    if status.media_url:
        return status.media_url
    return ''


def _serialize_status(status, for_user=None):
    """Serialize status for API."""
    viewer_ids = list(status.views.values_list('viewer_id', flat=True))
    view_count = len(viewer_ids)

    is_viewed = False
    if for_user:
        is_viewed = for_user.id in viewer_ids

    # Group statuses by user later in caller
    return {
        'id': status.id,
        'user_id': status.user.id,
        'username': status.user.username,
        'user_avatar': _get_avatar_url(status.user),
        'status_type': status.status_type,
        'content': status.content,
        'background_color': status.background_color,
        'font_style': status.font_style,
        'media_url': _status_media_url(status),
        'caption': status.caption,
        'created_at': timezone.localtime(status.created_at).isoformat(),
        'created_at_display': _format_status_time(status.created_at),
        'expires_at': status.expires_at.isoformat(),
        'view_count': view_count,
        'is_viewed': is_viewed,
    }


def _get_avatar_url(user):
    """Get user avatar URL."""
    try:
        profile = user.profile
        if profile.avatar:
            return profile.avatar.url
    except Exception:
        pass
    return ''


def _format_status_time(dt):
    """Format status time (e.g., '5 min ago')."""
    now = timezone.now()
    diff = (now - dt).total_seconds()

    if diff < 60:
        return 'just now'
    elif diff < 3600:
        mins = int(diff // 60)
        return f'{mins} min ago'
    elif diff < 86400:
        hours = int(diff // 3600)
        return f'{hours}h ago'
    else:
        days = int(diff // 86400)
        return f'{days}d ago'


def _get_friend_ids(user):
    """Get list of accepted friend IDs."""
    pairs = FriendRequest.objects.filter(
        Q(sender=user, status='accepted') | Q(receiver=user, status='accepted')
    ).values_list('sender_id', 'receiver_id')

    friend_ids = set()
    for s, r in pairs:
        friend_ids.add(r if s == user.id else s)
    friend_ids.discard(user.id)
    return list(friend_ids)


# ============================================================
# STATUS LIST / CREATE / DELETE
# ============================================================

@login_required
def list_statuses(request):
    """
    Get all statuses:
    - my_statuses: current user's own statuses
    - recent_updates: friends' statuses (grouped by user)
    - viewed_updates: friends' viewed statuses
    """
    now = timezone.now()
    user = request.user

    # Clean up expired (also done by Celery, but belt & suspenders)
    Status.objects.filter(expires_at__lte=now).delete()

    # My statuses
    my_statuses_qs = Status.objects.filter(
        user=user, expires_at__gt=now
    ).order_by('created_at')

    my_statuses = [_serialize_status(s, user) for s in my_statuses_qs]

    # Friends' statuses
    friend_ids = _get_friend_ids(user)
    friends_statuses_qs = Status.objects.filter(
        user_id__in=friend_ids, expires_at__gt=now
    ).select_related('user').order_by('user_id', 'created_at')

    # Group by user
    grouped = {}
    for s in friends_statuses_qs:
        uid = s.user_id
        if uid not in grouped:
            grouped[uid] = {
                'user_id': uid,
                'username': s.user.username,
                'user_avatar': _get_avatar_url(s.user),
                'statuses': [],
            }
        grouped[uid]['statuses'].append(_serialize_status(s, user))

    # Split into "recent" (unviewed) and "viewed"
    recent = []
    viewed = []

    for uid, data in grouped.items():
        all_viewed = all(s['is_viewed'] for s in data['statuses'])
        latest_at = max(s['created_at'] for s in data['statuses'])
        data['latest_at'] = latest_at
        data['total_count'] = len(data['statuses'])
        data['unviewed_count'] = sum(1 for s in data['statuses'] if not s['is_viewed'])

        if all_viewed:
            viewed.append(data)
        else:
            recent.append(data)

    # Sort recent by latest_at (newest first)
    recent.sort(key=lambda x: x['latest_at'], reverse=True)
    viewed.sort(key=lambda x: x['latest_at'], reverse=True)

    return JsonResponse({
        'status': 'ok',
        'my_statuses': my_statuses,
        'recent_updates': recent,
        'viewed_updates': viewed,
    })


@csrf_exempt
@login_required
def create_status(request):
    """
    Create a new status.
    - Text: content + background_color
    - Image: image file + optional caption
    - Video: video file + optional caption
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    status_type = request.POST.get('status_type', 'text')
    if status_type not in ('text', 'image', 'video'):
        return JsonResponse({'status': 'fail', 'message': 'Invalid status type'})

    expires_at = timezone.now() + timedelta(hours=STATUS_EXPIRY_HOURS)
    user = request.user

    if status_type == 'text':
        content = request.POST.get('content', '').strip()
        if not content:
            return JsonResponse({'status': 'fail', 'message': 'Content required'})
        if len(content) > 700:
            return JsonResponse({'status': 'fail', 'message': 'Max 700 characters'})

        background_color = request.POST.get('background_color', '#25D366')
        font_style = request.POST.get('font_style', 'normal')

        status_obj = Status.objects.create(
            user=user,
            status_type='text',
            content=content,
            background_color=background_color,
            font_style=font_style,
            expires_at=expires_at,
        )

    elif status_type in ('image', 'video'):
        media = request.FILES.get('media')
        caption = request.POST.get('caption', '').strip()

        if not media:
            return JsonResponse({'status': 'fail', 'message': 'Media required'})

        # Size limit: 25MB
        if media.size > 25 * 1024 * 1024:
            return JsonResponse({'status': 'fail', 'message': 'Max 25MB'})

        # Determine extension
        ext = os.path.splitext(media.name)[1].lower()
        if status_type == 'image' and not ext:
            ext = '.jpg'
        elif status_type == 'video' and not ext:
            ext = '.mp4'

        # Save to media/status/
        filename = f"status/{uuid.uuid4().hex}{ext}"
        saved_path = default_storage.save(filename, ContentFile(media.read()))
        media_url = settings.MEDIA_URL + saved_path

        status_obj = Status.objects.create(
            user=user,
            status_type=status_type,
            media_url=media_url,
            caption=caption[:500] if caption else '',
            expires_at=expires_at,
        )

    else:
        return JsonResponse({'status': 'fail', 'message': 'Unknown status type'})

    # Broadcast to friends (realtime)
    _broadcast_new_status(status_obj)

    return JsonResponse({
        'status': 'ok',
        'status_id': status_obj.id,
        'message': 'Status posted',
    })


def _broadcast_new_status(status_obj):
    """Notify friends about new status."""
    try:
        friend_ids = _get_friend_ids(status_obj.user)
        channel_layer = get_channel_layer()

        payload = {
            'type': 'new_status_event',
            'status_id': status_obj.id,
            'user_id': status_obj.user.id,
            'username': status_obj.user.username,
            'user_avatar': _get_avatar_url(status_obj.user),
            'status_type': status_obj.status_type,
        }

        for fid in friend_ids:
            try:
                async_to_sync(channel_layer.group_send)(f"user_{fid}", payload)
            except Exception as e:
                print(f"[STATUS WS ERROR {fid}] {e}")
    except Exception as e:
        print(f"[STATUS BROADCAST ERROR] {e}")




# ============================================================
# VIEW / VIEWERS / DELETE
# ============================================================

@csrf_exempt
@login_required
def view_status(request, status_id):
    """
    Mark a status as viewed by current user.
    Cannot view own status (returns ok without creating view).
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        status_obj = Status.objects.get(id=status_id)
    except Status.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Status not found'})

    # Expired check
    if status_obj.expires_at <= timezone.now():
        return JsonResponse({'status': 'fail', 'message': 'Status expired'})

    # Cannot view own status
    if status_obj.user_id == request.user.id:
        return JsonResponse({'status': 'ok', 'already_owner': True})

    # Friend check (privacy)
    friend_ids = _get_friend_ids(request.user)
    if status_obj.user_id not in friend_ids:
        return JsonResponse({'status': 'fail', 'message': 'Not allowed'})

    # Create view if not exists
    view_obj, created = StatusView.objects.get_or_create(
        status=status_obj,
        viewer=request.user,
    )

    # Notify status owner (realtime)
    if created:
        try:
            channel_layer = get_channel_layer()
            payload = {
                'type': 'status_view_event',
                'status_id': status_obj.id,
                'viewer_id': request.user.id,
                'viewer_username': request.user.username,
                'viewer_avatar': _get_avatar_url(request.user),
            }
            async_to_sync(channel_layer.group_send)(
                f"user_{status_obj.user_id}", payload
            )
        except Exception as e:
            print(f"[STATUS VIEW WS ERROR] {e}")

    return JsonResponse({
        'status': 'ok',
        'viewed': True,
        'new': created,
    })


@login_required
def get_status_viewers(request, status_id):
    """
    Get list of viewers for a status.
    Only owner can see viewers.
    """
    try:
        status_obj = Status.objects.get(id=status_id)
    except Status.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Status not found'})

    if status_obj.user_id != request.user.id:
        return JsonResponse({'status': 'fail', 'message': 'Not allowed'})

    views_qs = status_obj.views.select_related('viewer').order_by('-viewed_at')

    viewers = []
    for v in views_qs:
        viewers.append({
            'id': v.viewer.id,
            'username': v.viewer.username,
            'avatar_url': _get_avatar_url(v.viewer),
            'viewed_at': timezone.localtime(v.viewed_at).isoformat(),
            'viewed_at_display': _format_status_time(v.viewed_at),
        })

    return JsonResponse({
        'status': 'ok',
        'viewers': viewers,
        'total_count': len(viewers),
    })


@csrf_exempt
@login_required
def delete_status(request, status_id):
    """
    Delete own status.
    Also deletes the media file from storage.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        status_obj = Status.objects.get(id=status_id)
    except Status.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Status not found'})

    if status_obj.user_id != request.user.id:
        return JsonResponse({'status': 'fail', 'message': 'Not allowed'})

    # Delete media file
    if status_obj.media_url:
        try:
            media_path = status_obj.media_url.replace(settings.MEDIA_URL, '', 1)
            default_storage.delete(media_path)
        except Exception as e:
            print(f"[STATUS MEDIA DELETE ERROR] {e}")

    status_obj.delete()

    # Broadcast delete to friends (realtime)
    try:
        friend_ids = _get_friend_ids(request.user)
        channel_layer = get_channel_layer()
        payload = {
            'type': 'status_deleted_event',
            'status_id': status_id,
            'user_id': request.user.id,
        }
        for fid in friend_ids:
            try:
                async_to_sync(channel_layer.group_send)(f"user_{fid}", payload)
            except Exception as e:
                print(f"[STATUS DELETE WS ERROR {fid}] {e}")
    except Exception as e:
        print(f"[STATUS DELETE BROADCAST ERROR] {e}")

    return JsonResponse({'status': 'ok', 'message': 'Status deleted'})


@csrf_exempt
@login_required
def delete_all_my_statuses(request):
    """Delete all own statuses."""
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    my_statuses = Status.objects.filter(user=request.user)
    count = my_statuses.count()

    # Delete media files
    for s in my_statuses:
        if s.media_url:
            try:
                media_path = s.media_url.replace(settings.MEDIA_URL, '', 1)
                default_storage.delete(media_path)
            except Exception as e:
                print(f"[STATUS MEDIA DELETE ERROR] {e}")

    my_statuses.delete()

    return JsonResponse({
        'status': 'ok',
        'deleted_count': count,
    })


# ============================================================
# REPLY TO STATUS (starts chat with sender)
# ============================================================

@csrf_exempt
@login_required
def reply_to_status(request, status_id):
    """
    Reply to a status. Sends a chat message to the status owner.
    Creates ChatContact if not exists.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        status_obj = Status.objects.get(id=status_id)
    except Status.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Status not found'})

    if status_obj.user_id == request.user.id:
        return JsonResponse({'status': 'fail', 'message': 'Cannot reply to own status'})

    # Friend check
    friend_ids = _get_friend_ids(request.user)
    if status_obj.user_id not in friend_ids:
        return JsonResponse({'status': 'fail', 'message': 'Not allowed'})

    message = request.POST.get('message', '').strip()
    if not message:
        return JsonResponse({'status': 'fail', 'message': 'Message required'})
    if len(message) > 500:
        return JsonResponse({'status': 'fail', 'message': 'Max 500 characters'})

    # Create ChatContact both ways
    ChatContact.objects.get_or_create(user=request.user, contact=status_obj.user)
    ChatContact.objects.get_or_create(user=status_obj.user, contact=request.user)

    # Create message with reference to status
    from .models import Message

    # Compose content with status reference
    status_preview = ''
    if status_obj.status_type == 'text':
        status_preview = (status_obj.content or '')[:60]
    elif status_obj.status_type == 'image':
        status_preview = '📷 Photo'
    elif status_obj.status_type == 'video':
        status_preview = '🎥 Video'

    content = f'↩️ Replied to status: "{status_preview}"\n\n{message}'

    msg = Message.objects.create(
        sender=request.user,
        receiver=status_obj.user,
        content=content,
        message_type='text',
    )

    # Broadcast via WebSocket
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync as _a2s

        now = timezone.localtime(msg.timestamp)
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
            'type': 'chat_message',
            'message': {
                'id': msg.id,
                'sender': request.user.username,
                'sender_id': request.user.id,
                'receiver_id': status_obj.user.id,
                'content': content,
                'timestamp': now.strftime('%I:%M %p'),
                'date_iso': msg_date.strftime('%Y-%m-%d'),
                'date_display': date_display,
                'reply_to': None,
                'forwarded': False,
                'image_url': None,
                'audio_url': None,
                'audio_duration': '',
                'video_url': None,
                'video_duration': '',
                'document_url': None,
                'document_name': '',
                'document_size': '',
                'is_read': False,
                'expires_at': None,
                'message_type': 'text',
                'call_duration': '',
                'is_edited': False,
                'edited_at': '',
            }
        }
        _a2s(channel_layer.group_send)(f"user_{status_obj.user_id}", payload)
        _a2s(channel_layer.group_send)(f"user_{request.user.id}", payload)
    except Exception as e:
        print(f"[REPLY WS ERROR] {e}")

    return JsonResponse({
        'status': 'ok',
        'message_id': msg.id,
        'message': 'Reply sent',
    })


# ============================================================
# STATUS INFO (single status detail)
# ============================================================

@login_required
def get_status_info(request, status_id):
    """Get single status detail."""
    try:
        status_obj = Status.objects.get(id=status_id)
    except Status.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Status not found'})

    # Access check
    if status_obj.user_id != request.user.id:
        friend_ids = _get_friend_ids(request.user)
        if status_obj.user_id not in friend_ids:
            return JsonResponse({'status': 'fail', 'message': 'Not allowed'})

    data = _serialize_status(status_obj, request.user)

    # Add owner info
    data['owner'] = {
        'id': status_obj.user.id,
        'username': status_obj.user.username,
        'avatar_url': _get_avatar_url(status_obj.user),
    }

    # Add viewer list (only for owner)
    if status_obj.user_id == request.user.id:
        viewers_qs = status_obj.views.select_related('viewer').order_by('-viewed_at')
        data['viewers'] = [
            {
                'id': v.viewer.id,
                'username': v.viewer.username,
                'avatar_url': _get_avatar_url(v.viewer),
                'viewed_at_display': _format_status_time(v.viewed_at),
            }
            for v in viewers_qs
        ]

    return JsonResponse({'status': 'ok', 'data': data})