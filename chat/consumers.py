# chat/consumers.py
import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from datetime import timedelta


ONLINE_USERS = set()
ACTIVE_CALLS = {}
RING_TIMEOUT_SECONDS = 30


class ChatConsumer(AsyncWebsocketConsumer):
    # ============================================================
    # CONNECT / DISCONNECT
    # ============================================================
    async def connect(self):
        self.user = self.scope['user']
        if self.user.is_anonymous:
            await self.close()
            return

        self.personal_group = f"user_{self.user.id}"
        await self.channel_layer.group_add(self.personal_group, self.channel_name)
        await self.channel_layer.group_add("online_users", self.channel_name)
        await self.accept()

        ONLINE_USERS.add(self.user.id)
        await self.update_last_seen()

        # Broadcast online status
        await self.channel_layer.group_send("online_users", {
            "type": "user_status",
            "user_id": self.user.id,
            "username": self.user.username,
            "status": "online",
            "last_seen": "",
        })

        # Send current online list
        online_ids = list(ONLINE_USERS)
        await self.send(text_data=json.dumps({
            "type": "online_list",
            "user_ids": online_ids,
        }))

    async def disconnect(self, close_code):
        if not hasattr(self, 'user') or self.user.is_anonymous:
            return
        ONLINE_USERS.discard(self.user.id)
        await self.update_last_seen()

        try:
            await self.channel_layer.group_send("online_users", {
                "type": "user_status",
                "user_id": self.user.id,
                "username": self.user.username,
                "status": "offline",
                "last_seen": "",
            })
            await self.channel_layer.group_discard(self.personal_group, self.channel_name)
            await self.channel_layer.group_discard("online_users", self.channel_name)
        except Exception as e:
            print(f"[DISCONNECT ERROR] {e}")

    @database_sync_to_async
    def update_last_seen(self):
        from django.utils import timezone
        from .models import UserProfile
        try:
            p = UserProfile.objects.get(user=self.user)
            p.last_seen = timezone.now()
            p.save(update_fields=['last_seen'])
        except UserProfile.DoesNotExist:
            pass

    # ============================================================
    # RECEIVE — WS → server (all actions)
    # ============================================================
    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except Exception:
            return
        action = data.get('action')
        if not action:
            return

        try:
            if action == 'send_message':
                await self.handle_send_message(data)
            elif action == 'send_group_message':
                await self.handle_send_group_message(data)
            elif action == 'mark_read':
                await self.handle_mark_read(data)
            elif action == 'typing':
                await self.handle_typing(data)
            elif action == 'stop_typing':
                await self.handle_stop_typing(data)
            elif action == 'group_typing':
                await self.handle_group_typing(data)
            elif action == 'add_reaction':
                await self.handle_add_reaction(data)
            elif action == 'delete_for_everyone':
                await self.handle_delete_for_everyone(data)
            elif action == 'edit_message':
                await self.handle_edit_message(data)
            elif action == 'forward_message':
                await self.handle_forward_message(data)
            elif action == 'group_delete_for_everyone':
                await self.handle_group_delete_for_everyone(data)
            elif action == 'group_edit_message':
                await self.handle_group_edit_message(data)
            elif action == 'toggle_mute':
                await self.handle_toggle_flag(data, 'is_muted')
            elif action == 'toggle_pin':
                await self.handle_toggle_flag(data, 'is_pinned')
            elif action == 'toggle_archive':
                await self.handle_toggle_flag(data, 'is_archived')
            elif action == 'friend_request_accepted':
                await self.handle_friend_accepted(data)
        except Exception as e:
            print(f"[RECEIVE ERROR] action={action} err={e}")

    # ============================================================
    # AI REPLY LOGIC — DISABLED (moved to chat_ai/views.py)
    # ============================================================
    # Note: AI reply handling is now done by the /ai/chat/ HTTP endpoint 
    # in the new chat_ai app. The old WebSocket AI logic is disabled below 
    # to prevent double replies.
    #
    # @database_sync_to_async
    # def _is_ai_user(self, user_id):
    #     ...
    #
    # async def _send_ai_reply(self, ai_user_id, user_message):
    #     ...

    # ============================================================
    # EVENT HANDLERS (WS → client)
    # ============================================================
    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat_message",
            "message": event["message"],
        }))

    async def message_edited(self, event):
        await self.send(text_data=json.dumps({
            "type": "message_edited",
            "message_id": event["message_id"],
            "content": event["content"],
            "edited_at": event.get("edited_at", ""),
            "sender_id": event.get("sender_id"),
            "receiver_id": event.get("receiver_id"),
        }))

    async def message_deleted(self, event):
        await self.send(text_data=json.dumps({
            "type": "message_deleted",
            "message_id": event["message_id"],
            "sender_id": event.get("sender_id"),
            "receiver_id": event.get("receiver_id"),
        }))

    async def message_expired(self, event):
        await self.send(text_data=json.dumps({
            "type": "message_expired",
            "message_id": event["message_id"],
        }))

    async def typing_indicator(self, event):
        await self.send(text_data=json.dumps({
            "type": "typing",
            "from": event["from"],
        }))

    async def stop_typing_indicator(self, event):
        await self.send(text_data=json.dumps({
            "type": "stop_typing",
            "from": event["from"],
        }))

    async def messages_read_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "messages_read",
            "reader_id": event["reader_id"],
            "sender_id": event.get("sender_id"),
        }))

    async def user_status(self, event):
        await self.send(text_data=json.dumps({
            "type": "user_status",
            "user_id": event["user_id"],
            "username": event["username"],
            "status": event["status"],
            "last_seen": event.get("last_seen", ""),
        }))

    async def reaction_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "reaction_updated",
            "message_id": event["message_id"],
            "reactions": event["reactions"],
        }))

    async def disappearing_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "disappearing_updated",
            "seconds": event["seconds"],
            "user_id": event["user_id"],
            "other_id": event["other_id"],
        }))

    async def contact_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "contact_updated",
            "contact_id": event["contact_id"],
            "is_muted": event.get("is_muted"),
            "is_pinned": event.get("is_pinned"),
            "is_archived": event.get("is_archived"),
            "sound_enabled": event.get("sound_enabled"),
        }))

    async def wallpaper_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "wallpaper_updated",
            "user_id": event.get("user_id"),
            "other_id": event.get("other_id"),
            "wallpaper": event.get("wallpaper", ""),
        }))

    async def new_friend_request_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "new_friend_request",
            "from_user_id": event["from_user_id"],
            "from_username": event["from_username"],
            "from_avatar": event.get("from_avatar", ""),
        }))

    async def friend_request_accepted_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "friend_request_accepted",
            "by_user_id": event["by_user_id"],
            "by_username": event["by_username"],
            "avatar_url": event.get("avatar_url", ""),
        }))

    async def users_updated_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "users_updated",
            "user_id": event["user_id"],
            "username": event.get("username", ""),
            "avatar_url": event.get("avatar_url", ""),
        }))

    async def group_message_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "group_message",
            "room_id": event["room_id"],
            "message": event.get("message"),
            "system": event.get("system", False),
            "content": event.get("content", ""),
        }))

    async def group_updated_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "group_updated",
            "room_id": event["room_id"],
            "name": event.get("name", ""),
        }))

    async def group_members_updated_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "group_members_updated",
            "room_id": event["room_id"],
        }))

    async def group_deleted_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "group_deleted",
            "room_id": event["room_id"],
        }))

    async def removed_from_group_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "removed_from_group",
            "room_id": event["room_id"],
            "room_name": event.get("room_name", ""),
        }))

    async def group_typing_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "group_typing",
            "room_id": event["room_id"],
            "from_user_id": event.get("from_user_id"),
            "from_username": event.get("from_username", ""),
        }))

    async def group_message_deleted_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "group_message_deleted",
            "room_id": event["room_id"],
            "message_id": event["message_id"],
        }))

    async def group_message_edited_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "group_message_edited",
            "room_id": event["room_id"],
            "message_id": event["message_id"],
            "content": event.get("content", ""),
        }))

    # ============================================================
    # SEND MESSAGE
    # ============================================================
    async def handle_send_message(self, data):
        receiver_id = data.get('receiver_id')
        content = (data.get('content') or '').strip()
        reply_to_id = data.get('reply_to_id')
        if not receiver_id or not content:
            return
        result = await self._create_message(receiver_id, content, reply_to_id)
        if not result:
            return
        m, receiver, is_ai = result
        payload = {
            "type": "chat_message",
            "message": {
                "id": m['id'],
                "sender": self.user.username,
                "sender_id": self.user.id,
                "receiver_id": receiver_id,
                "content": content,
                "timestamp": m['timestamp'],
                "date_iso": m['date_iso'],
                "date_display": m['date_display'],
                "reply_to": m.get('reply_to'),
                "forwarded": False,
                "image_url": None,
                "audio_url": None,
                "audio_duration": "",
                "video_url": None,
                "video_duration": "",
                "document_url": None,
                "document_name": "",
                "document_size": "",
                "is_read": False,
                "expires_at": m.get('expires_at'),
                "message_type": "text",
                "call_duration": "",
                "is_edited": False,
                "edited_at": "",
                "is_pinned": False,
            }
        }
        await self.channel_layer.group_send(f"user_{receiver_id}", payload)
        await self.channel_layer.group_send(f"user_{self.user.id}", payload)

        # ⚠️ OLD AI LOGIC DISABLED HERE
        # if is_ai:
        #     asyncio.create_task(self._send_ai_reply(receiver_id, content))

    @database_sync_to_async
    def _create_message(self, receiver_id, content, reply_to_id=None):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from .models import Message, ChatSettings
        try:
            receiver = User.objects.get(id=receiver_id)
        except User.DoesNotExist:
            return None
        reply_to = None
        if reply_to_id:
            try:
                reply_to = Message.objects.get(id=reply_to_id)
            except Message.DoesNotExist:
                pass
        expires_at = None
        try:
            cs = ChatSettings.objects.get(user=self.user, other_user=receiver)
            if cs.disappearing_seconds > 0:
                expires_at = timezone.now() + timedelta(seconds=cs.disappearing_seconds)
        except ChatSettings.DoesNotExist:
            pass
        m = Message.objects.create(
            sender=self.user, receiver=receiver, content=content,
            reply_to=reply_to, expires_at=expires_at,
        )
        now = timezone.localtime(m.timestamp)
        today = timezone.localtime(timezone.now()).date()
        msg_date = now.date()
        if msg_date == today:
            date_display = 'TODAY'
        elif msg_date == today - timedelta(days=1):
            date_display = 'YESTERDAY'
        else:
            date_display = now.strftime('%d-%m-%Y')
        reply_to_data = None
        if reply_to:
            reply_to_data = {
                'id': reply_to.id,
                'sender': reply_to.sender.username,
                'content': (reply_to.content or '')[:80],
            }
        is_ai = False
        try:
            is_ai = receiver.profile.is_ai_bot
        except Exception:
            is_ai = receiver.username == 'ChatFlow AI'
        return ({
            'id': m.id,
            'timestamp': now.strftime('%I:%M %p'),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
            'reply_to': reply_to_data,
            'expires_at': expires_at.isoformat() if expires_at else None,
        }, receiver, is_ai)

    # ============================================================
    # MARK READ
    # ============================================================
    async def handle_mark_read(self, data):
        sender_id = data.get('sender_id')
        if not sender_id:
            return
        await self._mark_read(sender_id)
        await self.channel_layer.group_send(f"user_{sender_id}", {
            "type": "messages_read_event",
            "reader_id": self.user.id,
            "sender_id": sender_id,
        })

    @database_sync_to_async
    def _mark_read(self, sender_id):
        from .models import Message
        Message.objects.filter(sender_id=sender_id, receiver=self.user, is_read=False).update(is_read=True)

    # ============================================================
    # TYPING
    # ============================================================
    async def handle_typing(self, data):
        receiver_id = data.get('receiver_id')
        if not receiver_id:
            return
        await self.channel_layer.group_send(f"user_{receiver_id}", {
            "type": "typing_indicator",
            "from": self.user.username,
        })

    async def handle_stop_typing(self, data):
        receiver_id = data.get('receiver_id')
        if not receiver_id:
            return
        await self.channel_layer.group_send(f"user_{receiver_id}", {
            "type": "stop_typing_indicator",
            "from": self.user.username,
        })

    async def handle_group_typing(self, data):
        room_id = data.get('room_id')
        if not room_id:
            return
        members = await self._get_room_members(room_id)
        for uid in members:
            if uid == self.user.id:
                continue
            await self.channel_layer.group_send(f"user_{uid}", {
                "type": "group_typing_event",
                "room_id": room_id,
                "from_user_id": self.user.id,
                "from_username": self.user.username,
            })

    @database_sync_to_async
    def _get_room_members(self, room_id):
        from .models import ChatRoom
        try:
            room = ChatRoom.objects.get(id=room_id)
            return list(room.members.values_list('user_id', flat=True))
        except ChatRoom.DoesNotExist:
            return []

    # ============================================================
    # REACTION
    # ============================================================
    async def handle_add_reaction(self, data):
        msg_id = data.get('message_id')
        emoji = data.get('emoji')
        if not msg_id or not emoji:
            return
        result = await self._add_reaction(msg_id, emoji)
        if not result:
            return
        reactions_list, sender_id, receiver_id = result
        payload = {
            "type": "reaction_updated",
            "message_id": msg_id,
            "reactions": reactions_list,
        }
        await self.channel_layer.group_send(f"user_{sender_id}", payload)
        if receiver_id:
            await self.channel_layer.group_send(f"user_{receiver_id}", payload)
        if self.user.id not in (sender_id, receiver_id):
            await self.channel_layer.group_send(f"user_{self.user.id}", payload)

    @database_sync_to_async
    def _add_reaction(self, msg_id, emoji):
        from .models import Message, MessageReaction
        try:
            msg = Message.objects.get(id=msg_id)
        except Message.DoesNotExist:
            return None

        existing = MessageReaction.objects.filter(
            message=msg, user=self.user, emoji=emoji
        ).first()

        if existing:
            existing.delete()
        else:
            if not msg.room_id:
                MessageReaction.objects.filter(message=msg, user=self.user).delete()
            MessageReaction.objects.create(message=msg, user=self.user, emoji=emoji)

        reactions_list = self._get_reactions_summary(msg)
        return (reactions_list, msg.sender_id, msg.receiver_id if msg.receiver_id else None)

    def _get_reactions_summary(self, msg):
        from .models import MessageReaction
        summary = {}
        for r in MessageReaction.objects.filter(message=msg).select_related('user'):
            if r.emoji not in summary:
                summary[r.emoji] = {
                    'emoji': r.emoji,
                    'count': 0,
                    'users': [],
                    'me': False,
                }
            summary[r.emoji]['count'] += 1
            summary[r.emoji]['users'].append(r.user.username)
            if r.user_id == self.user.id:
                summary[r.emoji]['me'] = True
        return list(summary.values())

    # ============================================================
    # DELETE / EDIT / FORWARD
    # ============================================================
    async def handle_delete_for_everyone(self, data):
        msg_id = data.get('message_id')
        receiver_id = data.get('receiver_id')
        if not msg_id:
            return
        ok = await self._delete_for_everyone(msg_id)
        if ok:
            for uid in [self.user.id, receiver_id]:
                if uid:
                    await self.channel_layer.group_send(f"user_{uid}", {
                        "type": "message_deleted",
                        "message_id": msg_id,
                        "sender_id": self.user.id,
                        "receiver_id": receiver_id,
                    })

    @database_sync_to_async
    def _delete_for_everyone(self, msg_id):
        from .models import Message
        try:
            m = Message.objects.get(id=msg_id, sender=self.user)
        except Message.DoesNotExist:
            return False
        m.deleted_for_everyone = True
        m.content = ''
        m.image_url = None
        m.audio_url = None
        m.audio_duration = ''
        m.video_url = None
        m.video_duration = ''
        m.document_url = None
        m.document_name = ''
        m.document_size = ''
        m.save()
        return True

    async def handle_edit_message(self, data):
        msg_id = data.get('message_id')
        content = (data.get('content') or '').strip()
        receiver_id = data.get('receiver_id')
        if not msg_id or not content:
            return
        ok = await self._edit_message(msg_id, content)
        if ok:
            for uid in [self.user.id, receiver_id]:
                if uid:
                    await self.channel_layer.group_send(f"user_{uid}", {
                        "type": "message_edited",
                        "message_id": msg_id,
                        "content": content,
                        "sender_id": self.user.id,
                        "receiver_id": receiver_id,
                    })

    @database_sync_to_async
    def _edit_message(self, msg_id, content):
        from .models import Message
        from django.utils import timezone
        try:
            m = Message.objects.get(id=msg_id, sender=self.user)
        except Message.DoesNotExist:
            return False
        m.content = content
        m.is_edited = True
        m.edited_at = timezone.now()
        m.save()
        return True

    async def handle_forward_message(self, data):
        receiver_id = data.get('receiver_id')
        if not receiver_id:
            return
        result = await self._forward_message(data)
        if not result:
            return
        m, receiver = result
        payload = {
            "type": "chat_message",
            "message": {
                "id": m['id'],
                "sender": self.user.username,
                "sender_id": self.user.id,
                "receiver_id": receiver_id,
                "content": m['content'],
                "timestamp": m['timestamp'],
                "date_iso": m['date_iso'],
                "date_display": m['date_display'],
                "reply_to": None,
                "forwarded": True,
                "image_url": m.get('image_url'),
                "audio_url": m.get('audio_url'),
                "audio_duration": m.get('audio_duration', ''),
                "video_url": m.get('video_url'),
                "video_duration": m.get('video_duration', ''),
                "document_url": m.get('document_url'),
                "document_name": m.get('document_name', ''),
                "document_size": m.get('document_size', ''),
                "is_read": False,
                "expires_at": None,
                "message_type": "text",
                "call_duration": "",
                "is_edited": False,
                "edited_at": "",
                "is_pinned": False,
            }
        }
        await self.channel_layer.group_send(f"user_{receiver_id}", payload)
        await self.channel_layer.group_send(f"user_{self.user.id}", payload)

    @database_sync_to_async
    def _forward_message(self, data):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from .models import Message
        try:
            receiver = User.objects.get(id=data.get('receiver_id'))
        except User.DoesNotExist:
            return None
        m = Message.objects.create(
            sender=self.user, receiver=receiver,
            content=data.get('content') or '',
            image_url=data.get('image_url'),
            audio_url=data.get('audio_url'),
            audio_duration=data.get('audio_duration') or '',
            video_url=data.get('video_url'),
            video_duration=data.get('video_duration') or '',
            document_url=data.get('document_url'),
            document_name=data.get('document_name') or '',
            document_size=data.get('document_size') or '',
            forwarded=True,
        )
        now = timezone.localtime(m.timestamp)
        today = timezone.localtime(timezone.now()).date()
        msg_date = now.date()
        if msg_date == today:
            date_display = 'TODAY'
        elif msg_date == today - timedelta(days=1):
            date_display = 'YESTERDAY'
        else:
            date_display = now.strftime('%d-%m-%Y')
        return ({
            'id': m.id,
            'content': m.content,
            'timestamp': now.strftime('%I:%M %p'),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
            'image_url': m.image_url,
            'audio_url': m.audio_url,
            'audio_duration': m.audio_duration,
            'video_url': m.video_url,
            'video_duration': m.video_duration,
            'document_url': m.document_url,
            'document_name': m.document_name,
            'document_size': m.document_size,
        }, receiver)

    # ============================================================
    # TOGGLE FLAG & FRIEND REQUEST
    # ============================================================
    async def handle_toggle_flag(self, data, flag):
        contact_id = data.get('contact_id')
        if not contact_id:
            return
        new_val = await self._toggle_flag(contact_id, flag)
        if new_val is not None:
            await self.channel_layer.group_send(f"user_{self.user.id}", {
                "type": "contact_updated",
                "contact_id": contact_id,
                flag: new_val,
            })

    @database_sync_to_async
    def _toggle_flag(self, contact_id, flag):
        from django.contrib.auth.models import User
        from .models import ChatContact
        try:
            contact = User.objects.get(id=contact_id)
        except User.DoesNotExist:
            return None
        obj, _ = ChatContact.objects.get_or_create(user=self.user, contact=contact)
        setattr(obj, flag, not getattr(obj, flag))
        obj.save()
        return getattr(obj, flag)

    async def handle_friend_accepted(self, data):
        sender_id = data.get('sender_id')
        if not sender_id:
            return
        await self.channel_layer.group_send(f"user_{sender_id}", {
            "type": "friend_request_accepted_event",
            "by_user_id": self.user.id,
            "by_username": self.user.username,
            "avatar_url": "",
        })

    # ============================================================
    # GROUP MESSAGE
    # ============================================================
    async def handle_send_group_message(self, data):
        room_id = data.get('room_id')
        content = (data.get('content') or '').strip()
        reply_to_id = data.get('reply_to_id')
        if not room_id or not content:
            return
        result = await self._create_group_message(room_id, content, reply_to_id)
        if not result:
            return
        m, member_ids = result
        for uid in member_ids:
            await self.channel_layer.group_send(f"user_{uid}", {
                "type": "group_message_event",
                "room_id": room_id,
                "message": {
                    "id": m['id'],
                    "sender": self.user.username,
                    "sender_id": self.user.id,
                    "content": content,
                    "timestamp": m['timestamp'],
                    "date_iso": m['date_iso'],
                    "date_display": m['date_display'],
                    "reply_to": m.get('reply_to'),
                    "message_type": "text",
                },
                "system": False,
                "content": "",
            })

    @database_sync_to_async
    def _create_group_message(self, room_id, content, reply_to_id=None):
        from django.utils import timezone
        from .models import Message, ChatRoom
        try:
            room = ChatRoom.objects.get(id=room_id)
        except ChatRoom.DoesNotExist:
            return None
        reply_to = None
        if reply_to_id:
            try:
                reply_to = Message.objects.get(id=reply_to_id)
            except Message.DoesNotExist:
                pass
        m = Message.objects.create(
            sender=self.user, content=content,
            room=room, reply_to=reply_to,
        )
        now = timezone.localtime(m.timestamp)
        today = timezone.localtime(timezone.now()).date()
        msg_date = now.date()
        if msg_date == today:
            date_display = 'TODAY'
        elif msg_date == today - timedelta(days=1):
            date_display = 'YESTERDAY'
        else:
            date_display = now.strftime('%d-%m-%Y')
        reply_to_data = None
        if reply_to:
            reply_to_data = {
                'id': reply_to.id,
                'sender': reply_to.sender.username,
                'content': (reply_to.content or '')[:80],
            }
        member_ids = list(room.members.values_list('user_id', flat=True))
        return ({
            'id': m.id,
            'timestamp': now.strftime('%I:%M %p'),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
            'reply_to': reply_to_data,
        }, member_ids)

    async def handle_group_delete_for_everyone(self, data):
        msg_id = data.get('message_id')
        room_id = data.get('room_id')
        if not msg_id or not room_id:
            return
        member_ids = await self._get_room_members(room_id)
        ok = await self._delete_for_everyone(msg_id)
        if ok:
            for uid in member_ids:
                await self.channel_layer.group_send(f"user_{uid}", {
                    "type": "group_message_deleted_event",
                    "room_id": room_id,
                    "message_id": msg_id,
                })

    async def handle_group_edit_message(self, data):
        msg_id = data.get('message_id')
        room_id = data.get('room_id')
        content = (data.get('content') or '').strip()
        if not msg_id or not room_id or not content:
            return
        member_ids = await self._get_room_members(room_id)
        ok = await self._edit_message(msg_id, content)
        if ok:
            for uid in member_ids:
                await self.channel_layer.group_send(f"user_{uid}", {
                    "type": "group_message_edited_event",
                    "room_id": room_id,
                    "message_id": msg_id,
                    "content": content,
                })


# ================================================================
# CALL CONSUMER
# ================================================================
class CallConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if self.user.is_anonymous:
            await self.close()
            return
        self.personal_group = f"user_{self.user.id}"
        await self.channel_layer.group_add(self.personal_group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'user') and not self.user.is_anonymous:
            try:
                await self.channel_layer.group_discard(self.personal_group, self.channel_name)
            except Exception:
                pass

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except Exception:
            return
        action = data.get('action')
        if not action:
            return
        try:
            if action == 'start_call':
                await self.handle_start_call(data)
            elif action == 'accept_call':
                await self.handle_accept_call(data)
            elif action == 'reject_call':
                await self.handle_reject_call(data)
            elif action == 'end_call':
                await self.handle_end_call(data)
            elif action == 'webrtc_signal':
                await self.handle_webrtc_signal(data)
        except Exception as e:
            print(f"[CALL ERROR] action={action} err={e}")

    async def handle_start_call(self, data):
        receiver_id = data.get('receiver_id')
        call_type = data.get('call_type', 'voice')
        if not receiver_id:
            return
        ACTIVE_CALLS[self.user.id] = {
            'peer': receiver_id,
            'type': call_type,
            'state': 'ringing',
        }
        await self.channel_layer.group_send(f"user_{receiver_id}", {
            "type": "call_event",
            "event": "incoming_call",
            "from_user_id": self.user.id,
            "from_username": self.user.username,
            "call_type": call_type,
        })
        await self.channel_layer.group_send(f"user_{self.user.id}", {
            "type": "call_event",
            "event": "call_ringing",
        })

    async def handle_accept_call(self, data):
        caller_id = data.get('caller_id')
        if not caller_id:
            return
        ACTIVE_CALLS[self.user.id] = {
            'peer': caller_id,
            'state': 'active',
        }
        await self.channel_layer.group_send(f"user_{caller_id}", {
            "type": "call_event",
            "event": "call_accepted",
            "by_user_id": self.user.id,
        })
        await self.channel_layer.group_send(f"user_{self.user.id}", {
            "type": "call_event",
            "event": "call_accepted_self",
        })

    async def handle_reject_call(self, data):
        caller_id = data.get('caller_id')
        if not caller_id:
            return
        ACTIVE_CALLS.pop(self.user.id, None)
        await self.channel_layer.group_send(f"user_{caller_id}", {
            "type": "call_event",
            "event": "call_rejected",
            "by_user_id": self.user.id,
        })

    async def handle_end_call(self, data):
        peer = None
        if self.user.id in ACTIVE_CALLS:
            peer = ACTIVE_CALLS[self.user.id].get('peer')
            ACTIVE_CALLS.pop(self.user.id, None)
        if peer:
            await self.channel_layer.group_send(f"user_{peer}", {
                "type": "call_event",
                "event": "call_ended",
            })
            ACTIVE_CALLS.pop(peer, None)

    async def handle_webrtc_signal(self, data):
        target_id = data.get('target_id')
        signal = data.get('signal')
        if not target_id or not signal:
            return
        await self.channel_layer.group_send(f"user_{target_id}", {
            "type": "call_event",
            "event": "webrtc_signal",
            "signal": signal,
            "from_user_id": self.user.id,
        })

    async def call_event(self, event):
        await self.send(text_data=json.dumps({
            "type": event.get("event"),
            "from_user_id": event.get("from_user_id"),
            "from_username": event.get("from_username"),
            "by_user_id": event.get("by_user_id"),
            "call_type": event.get("call_type"),
            "signal": event.get("signal"),
        }))