# chat/tasks.py
from celery import shared_task
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Message


@shared_task
def delete_expired_messages():
    """
    Delete messages whose expires_at has passed.
    Notifies both sender and receiver via WebSocket before deletion.
    """
    now = timezone.now()

    # Snapshot queryset as list to avoid count-after-delete issue
    expired_msgs = list(
        Message.objects.filter(
            expires_at__isnull=False,
            expires_at__lte=now,
        ).values('id', 'sender_id', 'receiver_id')
    )

    if not expired_msgs:
        return "Deleted 0 expired messages"

    channel_layer = get_channel_layer()
    deleted_count = 0

    for msg in expired_msgs:
        payload = {
            'type': 'message_expired',
            'message_id': msg['id'],
        }

        # Notify sender
        try:
            async_to_sync(channel_layer.group_send)(
                f"user_{msg['sender_id']}", payload
            )
        except Exception as e:
            print(f"[WS ERROR sender {msg['sender_id']}] {e}")

        # Notify receiver (may be None)
        receiver_id = msg.get('receiver_id')
        if receiver_id:
            try:
                async_to_sync(channel_layer.group_send)(
                    f"user_{receiver_id}", payload
                )
            except Exception as e:
                print(f"[WS ERROR receiver {receiver_id}] {e}")

        # Hard delete
        try:
            Message.objects.filter(id=msg['id']).delete()
            deleted_count += 1
        except Exception as e:
            print(f"[DB ERROR deleting msg {msg['id']}] {e}")

    print(f"[CELERY] Deleted {deleted_count} expired messages")
    return f"Deleted {deleted_count} expired messages"


# ============================================================
# ⭐ TIER 3: STATUS AUTO-EXPIRE
# ============================================================

@shared_task
def expire_statuses():
    """
    Delete expired statuses (24hr+ old).
    Runs every 5 minutes via Celery Beat.
    """
    from .models import Status

    now = timezone.now()
    expired = Status.objects.filter(expires_at__lte=now)

    count = expired.count()
    if count == 0:
        return "No expired statuses"

    # Optional: broadcast expiry to friends
    try:
        channel_layer = get_channel_layer()
        for status in expired:
            payload = {
                'type': 'status_expired_event',
                'status_id': status.id,
                'user_id': status.user_id,
            }
            # Get friends
            from .models import FriendRequest
            from django.db.models import Q
            pairs = FriendRequest.objects.filter(
                Q(sender=status.user, status='accepted') |
                Q(receiver=status.user, status='accepted')
            ).values_list('sender_id', 'receiver_id')
            friend_ids = set()
            for s, r in pairs:
                friend_ids.add(r if s == status.user_id else s)
            friend_ids.discard(status.user_id)

            for fid in friend_ids:
                try:
                    async_to_sync(channel_layer.group_send)(
                        f"user_{fid}", payload
                    )
                except Exception as e:
                    print(f"[STATUS EXPIRE WS ERROR] {e}")
    except Exception as e:
        print(f"[STATUS EXPIRE BROADCAST ERROR] {e}")

    expired.delete()
    print(f"[CELERY] Deleted {count} expired statuses")
    return f"Deleted {count} expired statuses"