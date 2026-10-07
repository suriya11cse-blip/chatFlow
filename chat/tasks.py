from celery import shared_task
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Message


@shared_task
def delete_expired_messages():
    now = timezone.now()
    expired_msgs = Message.objects.filter(
        expires_at__isnull=False,
        expires_at__lte=now
    )

    channel_layer = get_channel_layer()

    for msg in expired_msgs:
        payload = {
            'type': 'message_expired',
            'message_id': msg.id,
        }
        try:
            async_to_sync(channel_layer.group_send)(
                f"user_{msg.sender_id}", payload
            )
            async_to_sync(channel_layer.group_send)(
                f"user_{msg.receiver_id}", payload
            )
        except Exception as e:
            print(f"WS send error: {e}")

        msg.delete()

    return f"Deleted {expired_msgs.count()} expired messages"