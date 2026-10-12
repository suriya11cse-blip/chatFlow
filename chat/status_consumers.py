# chat/status_consumers.py
"""
Status WebSocket consumers — status event handlers.
"""
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class StatusConsumer(AsyncWebsocketConsumer):
    """
    Dedicated WebSocket consumer for status events.
    Optional — status events also work via ChatConsumer.
    Users join a "status_updates" group to receive status broadcasts.
    """

    async def connect(self):
        self.user = self.scope['user']
        if self.user.is_anonymous:
            await self.close()
            return

        self.status_group = "status_updates"
        self.personal_group = f"user_{self.user.id}"

        await self.channel_layer.group_add(self.status_group, self.channel_name)
        await self.channel_layer.group_add(self.personal_group, self.channel_name)
        await self.accept()

        # Send currently active statuses count
        active_count = await self._count_active_statuses()
        await self.send(text_data=json.dumps({
            "type": "status_init",
            "active_status_count": active_count,
        }))

    async def disconnect(self, code):
        if self.user.is_anonymous:
            return
        await self.channel_layer.group_discard(self.status_group, self.channel_name)
        await self.channel_layer.group_discard(self.personal_group, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')

        if action == 'ping':
            await self.send(text_data=json.dumps({"type": "pong"}))

        elif action == 'refresh_statuses':
            active_count = await self._count_active_statuses()
            await self.send(text_data=json.dumps({
                "type": "status_refresh",
                "active_status_count": active_count,
            }))

    @database_sync_to_async
    def _count_active_statuses(self):
        from django.utils import timezone
        from django.db.models import Q
        from .models import Status, FriendRequest

        now = timezone.now()
        user = self.user

        # Get friends
        pairs = FriendRequest.objects.filter(
            Q(sender=user, status='accepted') | Q(receiver=user, status='accepted')
        ).values_list('sender_id', 'receiver_id')

        friend_ids = set()
        for s, r in pairs:
            friend_ids.add(r if s == user.id else s)
        friend_ids.discard(user.id)

        # Count unviewed statuses from friends
        statuses = Status.objects.filter(
            user_id__in=list(friend_ids),
            expires_at__gt=now,
        )
        return statuses.count()

    # ============================================================
    # EVENT HANDLERS (WS → client)
    # ============================================================

    async def new_status_event(self, event):
        """Friend posted a new status."""
        await self.send(text_data=json.dumps({
            "type": "new_status",
            "status_id": event["status_id"],
            "user_id": event["user_id"],
            "username": event["username"],
            "user_avatar": event.get("user_avatar", ""),
            "status_type": event.get("status_type", "text"),
        }))

    async def status_view_event(self, event):
        """Someone viewed your status (you are the owner)."""
        await self.send(text_data=json.dumps({
            "type": "status_view",
            "status_id": event["status_id"],
            "viewer_id": event["viewer_id"],
            "viewer_username": event["viewer_username"],
            "viewer_avatar": event.get("viewer_avatar", ""),
        }))

    async def status_deleted_event(self, event):
        """Friend deleted a status."""
        await self.send(text_data=json.dumps({
            "type": "status_deleted",
            "status_id": event["status_id"],
            "user_id": event["user_id"],
        }))

    async def status_expired_event(self, event):
        """Status expired (broadcast)."""
        await self.send(text_data=json.dumps({
            "type": "status_expired",
            "status_id": event["status_id"],
            "user_id": event["user_id"],
        }))

    async def status_updated_event(self, event):
        """Generic status update (for refresh triggers)."""
        await self.send(text_data=json.dumps({
            "type": "status_updated",
            "user_id": event.get("user_id"),
        }))