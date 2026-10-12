# chat/group_views.py
"""
Group Chat feature — WhatsApp style.
- Create/delete group
- Add/remove members
- Admin controls (make admin, remove)
- Group messages
- Mentions (@user)
- Per-user flags (mute, pin, archive)
- Read tracking (last_read_at)
"""
import os
import uuid
import json
import re

from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db.models import Q, Count, Max, Prefetch
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import (
    ChatRoom, ChatRoomMember, Message, UserProfile,
    FriendRequest, ChatContact,
)


# ============================================================
# HELPERS
# ============================================================

def _get_avatar_url(user):
    try:
        p = UserProfile.objects.get(user=user)
        if p.avatar:
            return p.avatar.url
    except UserProfile.DoesNotExist:
        pass
    return ''


def _get_group_avatar_url(room):
    if room.avatar:
        return room.avatar.url
    return ''


def _get_group_unread_count(room, user):
    """
    Count unread messages in group for user, using last_read_at.
    Excludes: own messages, deleted-for-everyone, expired.
    """
    try:
        member = ChatRoomMember.objects.get(room=room, user=user)
    except ChatRoomMember.DoesNotExist:
        return 0

    qs = Message.objects.filter(
        room=room,
        deleted_for_everyone=False,
    ).exclude(sender=user).exclude(
        expires_at__isnull=False, expires_at__lte=timezone.now()
    )

    if member.last_read_at:
        qs = qs.filter(timestamp__gt=member.last_read_at)

    return qs.count()


def _serialize_member(member, request_user=None):
    return {
        'id': member.user.id,
        'username': member.user.username,
        'avatar_url': _get_avatar_url(member.user),
        'role': member.role,
        'is_admin': member.role == 'admin',
        'is_me': (request_user and member.user_id == request_user.id),
        'joined_at': timezone.localtime(member.joined_at).isoformat(),
        'joined_at_display': timezone.localtime(member.joined_at).strftime('%d %b %Y'),
    }


def _serialize_room(room, request_user, include_members=False):
    try:
        my_membership = ChatRoomMember.objects.get(room=room, user=request_user)
        my_role = my_membership.role
        my_is_muted = my_membership.is_muted
        my_is_pinned = my_membership.is_pinned
        my_is_archived = my_membership.is_archived
    except ChatRoomMember.DoesNotExist:
        my_role = 'member'
        my_is_muted = False
        my_is_pinned = False
        my_is_archived = False

    last_msg = Message.objects.filter(
        room=room, deleted_for_everyone=False
    ).order_by('-timestamp').first()

    if last_msg:
        last_preview = (last_msg.content or '')[:60]
        if last_msg.image_url:
            last_preview = '📷 Image'
        elif last_msg.audio_url:
            last_preview = '🎤 Voice message'
        elif last_msg.video_url:
            last_preview = '🎥 Video'
        elif last_msg.document_url:
            last_preview = '📄 Document'

        last_ts = last_msg.timestamp
        last_sender = last_msg.sender.username
        last_sender_id = last_msg.sender_id
    else:
        last_preview = ''
        last_ts = None
        last_sender = ''
        last_sender_id = None

    data = {
        'id': room.id,
        'name': room.name,
        'description': room.description,
        'avatar_url': _get_group_avatar_url(room),
        'room_type': room.room_type,
        'member_count': room.member_count,
        'created_by_id': room.created_by_id if room.created_by else None,
        'created_by_username': room.created_by.username if room.created_by else '',
        'created_at': timezone.localtime(room.created_at).isoformat(),
        'updated_at': timezone.localtime(room.updated_at).isoformat(),
        'last_message': last_preview,
        'last_message_sender': last_sender,
        'last_message_sender_id': last_sender_id,
        'last_message_ts': last_ts.isoformat() if last_ts else None,
        'last_message_ts_display': _format_msg_time(last_ts) if last_ts else '',
        'my_role': my_role,
        'is_admin': my_role == 'admin',
        'is_muted': my_is_muted,
        'is_pinned': my_is_pinned,
        'is_archived': my_is_archived,
        'unread_count': _get_group_unread_count(room, request_user),
    }

    if include_members:
        members_qs = room.members.select_related('user').order_by('role', 'joined_at')
        data['members'] = [_serialize_member(m, request_user) for m in members_qs]

    return data


def _format_msg_time(dt):
    if not dt:
        return ''
    now = timezone.localtime(timezone.now())
    local = timezone.localtime(dt)
    today = now.date()
    msg_date = local.date()

    if msg_date == today:
        return local.strftime('%I:%M %p')
    elif msg_date == today - timezone.timedelta(days=1):
        return 'Yesterday'
    else:
        return local.strftime('%d/%m/%Y')


def _broadcast_group_event(room_id, payload, exclude_user_id=None):
    try:
        channel_layer = get_channel_layer()
        members = ChatRoomMember.objects.filter(room_id=room_id).values_list('user_id', flat=True)
        for uid in members:
            if exclude_user_id and uid == exclude_user_id:
                continue
            try:
                async_to_sync(channel_layer.group_send)(f"user_{uid}", payload)
            except Exception as e:
                print(f"[GROUP WS ERROR user {uid}] {e}")
    except Exception as e:
        print(f"[GROUP BROADCAST ERROR] {e}")


# ============================================================
# GROUP CREATE / LIST / INFO / UPDATE
# ============================================================

@csrf_exempt
@login_required
def create_group(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    name = (request.POST.get('name') or '').strip()
    description = (request.POST.get('description') or '').strip()
    member_ids_str = (request.POST.get('member_ids') or '').strip()

    if not name:
        return JsonResponse({'status': 'fail', 'message': 'Group name required'})
    if len(name) > 100:
        return JsonResponse({'status': 'fail', 'message': 'Name too long (max 100)'})

    member_ids = []
    if member_ids_str:
        for x in member_ids_str.split(','):
            x = x.strip()
            if x.isdigit():
                member_ids.append(int(x))

    member_ids = list(set(member_ids))
    member_ids = [m for m in member_ids if m != request.user.id]

    if len(member_ids) < 1:
        return JsonResponse({'status': 'fail', 'message': 'Add at least 1 member'})
    if len(member_ids) > 200:
        return JsonResponse({'status': 'fail', 'message': 'Max 200 members'})

    valid_users = User.objects.filter(id__in=member_ids)
    if valid_users.count() < 1:
        return JsonResponse({'status': 'fail', 'message': 'No valid users'})

    room = ChatRoom.objects.create(
        name=name,
        description=description[:500] if description else '',
        room_type='group',
        created_by=request.user,
    )

    avatar_file = request.FILES.get('avatar')
    if avatar_file:
        if avatar_file.size > 5 * 1024 * 1024:
            return JsonResponse({'status': 'fail', 'message': 'Avatar max 5MB'})
        ext = os.path.splitext(avatar_file.name)[1].lower() or '.jpg'
        filename = f"group_avatars/{uuid.uuid4().hex}{ext}"
        saved = default_storage.save(filename, ContentFile(avatar_file.read()))
        room.avatar = saved
        room.save(update_fields=['avatar'])

    ChatRoomMember.objects.create(
        room=room, user=request.user, role='admin', last_read_at=timezone.now()
    )

    for u in valid_users:
        ChatRoomMember.objects.get_or_create(
            room=room, user=u,
            defaults={'role': 'member'}
        )

    Message.objects.create(
        sender=request.user,
        room=room,
        content=f"📢 {request.user.username} created the group \"{name}\"",
        message_type='text',
    )

    payload = {
        'type': 'new_group_event',
        'room': _serialize_room(room, request.user, include_members=False),
    }
    _broadcast_group_event(room.id, payload)

    return JsonResponse({
        'status': 'ok',
        'room': _serialize_room(room, request.user, include_members=True),
        'message': 'Group created',
    })


@login_required
def list_groups(request):
    memberships = ChatRoomMember.objects.filter(
        user=request.user
    ).select_related('room').order_by('-room__updated_at')

    rooms = [m.room for m in memberships]

    pinned = []
    archived = []
    normal = []

    for room in rooms:
        data = _serialize_room(room, request.user)
        if data['is_archived']:
            archived.append(data)
        elif data['is_pinned']:
            pinned.append(data)
        else:
            normal.append(data)

    pinned.sort(key=lambda x: x['last_message_ts'] or '', reverse=True)
    normal.sort(key=lambda x: x['last_message_ts'] or '', reverse=True)
    archived.sort(key=lambda x: x['last_message_ts'] or '', reverse=True)

    return JsonResponse({
        'status': 'ok',
        'groups': pinned + normal + archived,
        'pinned_count': len(pinned),
        'archived_count': len(archived),
    })


@login_required
def group_info(request, room_id):
    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    if not ChatRoomMember.objects.filter(room=room, user=request.user).exists():
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    return JsonResponse({
        'status': 'ok',
        'room': _serialize_room(room, request.user, include_members=True),
    })


@csrf_exempt
@login_required
def update_group(request, room_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    try:
        me = ChatRoomMember.objects.get(room=room, user=request.user)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    if me.role != 'admin':
        return JsonResponse({'status': 'fail', 'message': 'Admin only'})

    name = (request.POST.get('name') or '').strip()
    description = (request.POST.get('description') or '').strip()

    if name:
        if len(name) > 100:
            return JsonResponse({'status': 'fail', 'message': 'Name too long'})
        room.name = name

    if description is not None:
        room.description = description[:500]

    avatar_file = request.FILES.get('avatar')
    if avatar_file:
        if avatar_file.size > 5 * 1024 * 1024:
            return JsonResponse({'status': 'fail', 'message': 'Avatar max 5MB'})
        if room.avatar:
            try:
                room.avatar.delete(save=False)
            except Exception:
                pass
        ext = os.path.splitext(avatar_file.name)[1].lower() or '.jpg'
        filename = f"group_avatars/{uuid.uuid4().hex}{ext}"
        saved = default_storage.save(filename, ContentFile(avatar_file.read()))
        room.avatar = saved

    room.save()

    payload = {
        'type': 'group_updated_event',
        'room_id': room.id,
        'name': room.name,
        'description': room.description,
        'avatar_url': _get_group_avatar_url(room),
    }
    _broadcast_group_event(room.id, payload)

    return JsonResponse({
        'status': 'ok',
        'room': _serialize_room(room, request.user, include_members=True),
        'message': 'Group updated',
    })


# ⬇️ PART B — next message la kudukkren


# ============================================================
# MEMBERS: ADD / REMOVE / ADMIN
# ============================================================

@csrf_exempt
@login_required
def add_members(request, room_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    try:
        me = ChatRoomMember.objects.get(room=room, user=request.user)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    member_ids_str = (request.POST.get('member_ids') or '').strip()
    if not member_ids_str:
        return JsonResponse({'status': 'fail', 'message': 'member_ids required'})

    member_ids = []
    for x in member_ids_str.split(','):
        x = x.strip()
        if x.isdigit():
            member_ids.append(int(x))

    member_ids = [m for m in member_ids if m != request.user.id]
    member_ids = list(set(member_ids))

    if not member_ids:
        return JsonResponse({'status': 'fail', 'message': 'No valid IDs'})

    existing_ids = set(
        ChatRoomMember.objects.filter(room=room).values_list('user_id', flat=True)
    )
    to_add = [m for m in member_ids if m not in existing_ids]

    if not to_add:
        return JsonResponse({'status': 'fail', 'message': 'All users already in group'})

    users = User.objects.filter(id__in=to_add)
    added_names = []

    for u in users:
        ChatRoomMember.objects.create(
            room=room, user=u, role='member',
            last_read_at=timezone.now()
        )
        added_names.append(u.username)

    names_str = ', '.join(added_names)
    Message.objects.create(
        sender=request.user,
        room=room,
        content=f"➕ {request.user.username} added {names_str}",
        message_type='text',
    )

    _broadcast_group_event(room.id, {
        'type': 'group_message_event',
        'room_id': room.id,
        'system': True,
        'content': f"➕ {request.user.username} added {names_str}",
        'message_id': None,
        'sender_id': request.user.id,
        'sender_username': request.user.username,
        'sender_avatar': _get_avatar_url(request.user),
    })

    _broadcast_group_event(room.id, {
        'type': 'group_members_updated_event',
        'room_id': room.id,
    })

    return JsonResponse({
        'status': 'ok',
        'added': added_names,
        'room': _serialize_room(room, request.user, include_members=True),
        'message': f'Added {len(added_names)} members',
    })


@csrf_exempt
@login_required
def remove_member(request, room_id, user_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    try:
        me = ChatRoomMember.objects.get(room=room, user=request.user)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    if me.role != 'admin':
        return JsonResponse({'status': 'fail', 'message': 'Admin only'})

    if user_id == request.user.id:
        return JsonResponse({'status': 'fail', 'message': 'Use leave endpoint for self'})

    try:
        target_member = ChatRoomMember.objects.get(room=room, user_id=user_id)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not in group'})

    target_user = target_member.user
    target_member.delete()

    Message.objects.create(
        sender=request.user,
        room=room,
        content=f"➖ {request.user.username} removed {target_user.username}",
        message_type='text',
    )

    _broadcast_group_event(room.id, {
        'type': 'group_message_event',
        'room_id': room.id,
        'system': True,
        'content': f"➖ {request.user.username} removed {target_user.username}",
        'message_id': None,
        'sender_id': request.user.id,
        'sender_username': request.user.username,
        'sender_avatar': _get_avatar_url(request.user),
    })

    _broadcast_group_event(room.id, {
        'type': 'group_members_updated_event',
        'room_id': room.id,
    })

    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(f"user_{user_id}", {
            'type': 'removed_from_group_event',
            'room_id': room.id,
            'room_name': room.name,
        })
    except Exception as e:
        print(f"[REMOVED NOTIFY ERROR] {e}")

    return JsonResponse({'status': 'ok', 'message': 'Member removed'})


@csrf_exempt
@login_required
def make_admin(request, room_id, user_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    try:
        me = ChatRoomMember.objects.get(room=room, user=request.user)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    if me.role != 'admin':
        return JsonResponse({'status': 'fail', 'message': 'Admin only'})

    try:
        target = ChatRoomMember.objects.get(room=room, user_id=user_id)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not in group'})

    if target.role == 'admin':
        return JsonResponse({'status': 'fail', 'message': 'Already admin'})

    target.role = 'admin'
    target.save(update_fields=['role'])

    Message.objects.create(
        sender=request.user,
        room=room,
        content=f"👑 {target.user.username} is now an admin",
        message_type='text',
    )

    _broadcast_group_event(room.id, {
        'type': 'group_members_updated_event',
        'room_id': room.id,
    })

    return JsonResponse({'status': 'ok', 'message': 'Admin added'})


@csrf_exempt
@login_required
def remove_admin(request, room_id, user_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    try:
        me = ChatRoomMember.objects.get(room=room, user=request.user)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    if me.role != 'admin':
        return JsonResponse({'status': 'fail', 'message': 'Admin only'})

    try:
        target = ChatRoomMember.objects.get(room=room, user_id=user_id)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'User not in group'})

    if target.role != 'admin':
        return JsonResponse({'status': 'fail', 'message': 'Not an admin'})

    admin_count = ChatRoomMember.objects.filter(room=room, role='admin').count()
    if admin_count <= 1:
        return JsonResponse({'status': 'fail', 'message': 'At least 1 admin required'})

    target.role = 'member'
    target.save(update_fields=['role'])

    _broadcast_group_event(room.id, {
        'type': 'group_members_updated_event',
        'room_id': room.id,
    })

    return JsonResponse({'status': 'ok', 'message': 'Admin removed'})


@csrf_exempt
@login_required
def leave_group(request, room_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    try:
        me = ChatRoomMember.objects.get(room=room, user=request.user)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    was_admin = me.role == 'admin'
    me.delete()

    remaining = ChatRoomMember.objects.filter(room=room)

    if not remaining.exists():
        Message.objects.filter(room=room).delete()
        room.delete()
        return JsonResponse({'status': 'ok', 'message': 'Group deleted (no members left)'})

    if was_admin:
        admins_left = remaining.filter(role='admin').count()
        if admins_left == 0:
            oldest = remaining.order_by('joined_at').first()
            oldest.role = 'admin'
            oldest.save(update_fields=['role'])

    Message.objects.create(
        sender=request.user,
        room=room,
        content=f"🚪 {request.user.username} left the group",
        message_type='text',
    )

    _broadcast_group_event(room.id, {
        'type': 'group_message_event',
        'room_id': room.id,
        'system': True,
        'content': f"🚪 {request.user.username} left the group",
        'message_id': None,
        'sender_id': request.user.id,
        'sender_username': request.user.username,
        'sender_avatar': _get_avatar_url(request.user),
    })

    _broadcast_group_event(room.id, {
        'type': 'group_members_updated_event',
        'room_id': room.id,
    })

    return JsonResponse({'status': 'ok', 'message': 'Left group'})


@csrf_exempt
@login_required
def delete_group(request, room_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    try:
        me = ChatRoomMember.objects.get(room=room, user=request.user)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    if me.role != 'admin':
        return JsonResponse({'status': 'fail', 'message': 'Admin only'})

    _broadcast_group_event(room.id, {
        'type': 'group_deleted_event',
        'room_id': room.id,
        'room_name': room.name,
        'deleted_by': request.user.username,
    })

    Message.objects.filter(room=room).delete()
    ChatRoomMember.objects.filter(room=room).delete()

    if room.avatar:
        try:
            room.avatar.delete(save=False)
        except Exception:
            pass

    room.delete()

    return JsonResponse({'status': 'ok', 'message': 'Group deleted'})


# ============================================================
# GROUP MESSAGES
# ============================================================

@login_required
def get_group_messages(request, room_id):
    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    if not ChatRoomMember.objects.filter(room=room, user=request.user).exists():
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    # ⭐ Mark as read
    now = timezone.now()
    ChatRoomMember.objects.filter(room=room, user=request.user).update(last_read_at=now)

    msgs = Message.objects.filter(
        room=room
    ).exclude(deleted_for=request.user).exclude(
        deleted_for_everyone=True
    ).exclude(
        expires_at__isnull=False, expires_at__lte=now
    ).order_by('timestamp')[:500]

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
        elif msg_date == today - timezone.timedelta(days=1):
            date_display = 'YESTERDAY'
        else:
            date_display = now_local.strftime('%d-%m-%Y')

        is_system = m.message_type == 'text' and (
            m.content.startswith('📢') or
            m.content.startswith('➕') or
            m.content.startswith('➖') or
            m.content.startswith('🚪') or
            m.content.startswith('👑')
        )

        try:
            mentioned_ids = list(m.mentioned_users.values_list('id', flat=True))
        except Exception:
            mentioned_ids = []

        data.append({
            'id': m.id,
            'sender': m.sender.username,
            'sender_id': m.sender.id,
            'sender_avatar': _get_avatar_url(m.sender),
            'content': '' if m.deleted_for_everyone else (m.content or ''),
            'timestamp': now_local.strftime('%I:%M %p'),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
            'is_mine': m.sender == request.user,
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
            'is_edited': getattr(m, 'is_edited', False),
            'is_system': is_system,
            'mentioned_user_ids': mentioned_ids,
        })

    return JsonResponse({'status': 'ok', 'messages': data, 'room_id': room_id})


@csrf_exempt
@login_required
def send_group_message(request, room_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    if not ChatRoomMember.objects.filter(room=room, user=request.user).exists():
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    content = (request.POST.get('content') or '').strip()
    if not content:
        return JsonResponse({'status': 'fail', 'message': 'Content required'})

    mentioned_ids = _parse_mentions(content)

    msg = Message.objects.create(
        sender=request.user,
        room=room,
        content=content,
        message_type='text',
    )
    if mentioned_ids:
        try:
            msg.mentioned_users.set(mentioned_ids)
        except Exception:
            pass

    room.updated_at = timezone.now()
    room.save(update_fields=['updated_at'])

    # ⭐ Mark sender as read
    ChatRoomMember.objects.filter(room=room, user=request.user).update(last_read_at=timezone.now())

    payload = {
        'type': 'group_message_event',
        'room_id': room.id,
        'system': False,
        'message': {
            'id': msg.id,
            'sender': request.user.username,
            'sender_id': request.user.id,
            'sender_avatar': _get_avatar_url(request.user),
            'content': content,
            'timestamp': timezone.localtime(msg.timestamp).strftime('%I:%M %p'),
            'date_iso': timezone.localtime(msg.timestamp).strftime('%Y-%m-%d'),
            'is_mine': False,
            'mentioned_user_ids': mentioned_ids,
        },
    }
    _broadcast_group_event(room.id, payload)

    return JsonResponse({'status': 'ok', 'message_id': msg.id})


def _parse_mentions(content):
    if not content:
        return []
    pattern = re.compile(r'@([A-Za-z0-9_]{1,30})')
    usernames = list(set(pattern.findall(content)))
    if not usernames:
        return []
    users = User.objects.filter(username__in=usernames)
    return list(users.values_list('id', flat=True))


# ============================================================
# PER-USER FLAGS (MUTE / PIN / ARCHIVE)
# ============================================================

@csrf_exempt
@login_required
def toggle_group_flag(request, room_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    try:
        me = ChatRoomMember.objects.get(room=room, user=request.user)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    flag = (request.POST.get('flag') or '').strip().lower()
    if flag not in ('mute', 'pin', 'archive'):
        return JsonResponse({'status': 'fail', 'message': 'Invalid flag'})

    if flag == 'mute':
        me.is_muted = not me.is_muted
        me.save(update_fields=['is_muted'])
        value = me.is_muted
    elif flag == 'pin':
        me.is_pinned = not me.is_pinned
        me.save(update_fields=['is_pinned'])
        value = me.is_pinned
    elif flag == 'archive':
        me.is_archived = not me.is_archived
        me.save(update_fields=['is_archived'])
        value = me.is_archived

    return JsonResponse({
        'status': 'ok',
        'flag': flag,
        'value': value,
    })


# ============================================================
# ⭐ MARK GROUP AS READ
# ============================================================

@csrf_exempt
@login_required
def mark_group_read(request, room_id):
    """Mark group as read for current user (reset unread)."""
    if request.method != 'POST':
        return JsonResponse({'status': 'fail', 'message': 'POST only'})

    try:
        room = ChatRoom.objects.get(id=room_id)
    except ChatRoom.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Group not found'})

    try:
        me = ChatRoomMember.objects.get(room=room, user=request.user)
    except ChatRoomMember.DoesNotExist:
        return JsonResponse({'status': 'fail', 'message': 'Not a member'})

    me.last_read_at = timezone.now()
    me.save(update_fields=['last_read_at'])

    return JsonResponse({'status': 'ok'})