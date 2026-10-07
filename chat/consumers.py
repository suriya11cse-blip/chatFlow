# chat/consumers.py
import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from datetime import timedelta

# ⚠️ Do NOT import models/User/timezone at module level.
# Daphne imports this file BEFORE Django apps are loaded → AppRegistryNotReady.
# Import them inside methods instead.


ONLINE_USERS = set()
ACTIVE_CALLS = {}
RING_TIMEOUT_SECONDS = 30


class ChatConsumer(AsyncWebsocketConsumer):
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

        await self.channel_layer.group_send("online_users", {
            "type": "user_status",
            "user_id": self.user.id,
            "username": self.user.username,
            "status": "online",
        })

        online_ids = list(ONLINE_USERS)
        await self.send(text_data=json.dumps({
            "type": "online_list",
            "user_ids": online_ids,
        }))

    async def disconnect(self, code):
        if self.user.is_anonymous:
            return
        ONLINE_USERS.discard(self.user.id)
        await self.channel_layer.group_discard(self.personal_group, self.channel_name)
        await self.channel_layer.group_send("online_users", {
            "type": "user_status",
            "user_id": self.user.id,
            "username": self.user.username,
            "status": "offline",
        })
        await self.channel_layer.group_discard("online_users", self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')

        if action == 'send_message':
            receiver_id = data.get('receiver_id')
            content = data.get('content', '').strip()
            reply_to_id = data.get('reply_to_id')
            if not content:
                return
            msg = await self.save_message(receiver_id, content, reply_to_id)

            payload = {
                "type": "chat_message",
                "message": {
                    "id": msg['id'],
                    "sender": self.user.username,
                    "sender_id": self.user.id,
                    "receiver_id": receiver_id,
                    "content": content,
                    "timestamp": msg['timestamp'],
                    "date_iso": msg['date_iso'],
                    "date_display": msg['date_display'],
                    "reply_to": msg['reply_to'],
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
                    "expires_at": msg['expires_at'],
                    "message_type": "text",
                    "call_duration": "",
                    "is_edited": False,
                    "edited_at": "",
                }
            }
            await self.channel_layer.group_send(f"user_{receiver_id}", payload)
            await self.channel_layer.group_send(f"user_{self.user.id}", payload)

            # ⭐ If receiver is AI bot → generate AI reply
            is_ai = await self._is_ai_user(receiver_id)
            if is_ai:
                asyncio.create_task(self._send_ai_reply(receiver_id, content))

        elif action == 'edit_message':
            msg_id = data.get('message_id')
            new_content = data.get('content', '').strip()
            receiver_id = data.get('receiver_id')
            if not new_content:
                return
            ok, edited_at_str = await self.edit_message(msg_id, new_content)
            if ok:
                payload = {
                    "type": "message_edited",
                    "message_id": msg_id,
                    "content": new_content,
                    "edited_at": edited_at_str,
                    "sender_id": self.user.id,
                    "receiver_id": int(receiver_id),
                }
                await self.channel_layer.group_send(f"user_{receiver_id}", payload)
                await self.channel_layer.group_send(f"user_{self.user.id}", payload)

        elif action == 'delete_for_everyone':
            msg_id = data.get('message_id')
            receiver_id = data.get('receiver_id')
            ok = await self.hard_delete(msg_id)
            if ok:
                payload = {
                    "type": "message_deleted",
                    "message_id": msg_id,
                    "sender_id": self.user.id,
                    "receiver_id": int(receiver_id),
                }
                await self.channel_layer.group_send(f"user_{receiver_id}", payload)
                await self.channel_layer.group_send(f"user_{self.user.id}", payload)

        elif action == 'typing':
            receiver_id = data.get('receiver_id')
            await self.channel_layer.group_send(f"user_{receiver_id}", {
                "type": "typing_indicator",
                "from": self.user.username,
            })

        elif action == 'stop_typing':
            receiver_id = data.get('receiver_id')
            await self.channel_layer.group_send(f"user_{receiver_id}", {
                "type": "stop_typing_indicator",
                "from": self.user.username,
            })

        elif action == 'mark_read':
            sender_id = data.get('sender_id')
            await self.mark_read(sender_id)
            await self.channel_layer.group_send(f"user_{sender_id}", {
                "type": "messages_read_event",
                "reader_id": self.user.id,
                "sender_id": sender_id,
            })

        elif action == 'add_reaction':
            msg_id = data.get('message_id')
            emoji = data.get('emoji')
            reactions_list, sender_id, receiver_id = await self.add_reaction(msg_id, emoji)
            if reactions_list is not None:
                payload = {
                    "type": "reaction_updated",
                    "message_id": msg_id,
                    "reactions": reactions_list,
                }
                await self.channel_layer.group_send(f"user_{sender_id}", payload)
                await self.channel_layer.group_send(f"user_{receiver_id}", payload)

        elif action == 'forward_message':
            receiver_id = data.get('receiver_id')
            content = data.get('content', '')
            image_url = data.get('image_url')
            audio_url = data.get('audio_url')
            audio_duration = data.get('audio_duration', '')
            video_url = data.get('video_url')
            video_duration = data.get('video_duration', '')
            document_url = data.get('document_url')
            document_name = data.get('document_name', '')
            document_size = data.get('document_size', '')
            msg = await self.save_forwarded_message(
                receiver_id, content, image_url, audio_url,
                audio_duration, video_url, video_duration,
                document_url, document_name, document_size
            )
            payload = {
                "type": "chat_message",
                "message": {
                    "id": msg['id'],
                    "sender": self.user.username,
                    "sender_id": self.user.id,
                    "receiver_id": receiver_id,
                    "content": content,
                    "timestamp": msg['timestamp'],
                    "date_iso": msg['date_iso'],
                    "date_display": msg['date_display'],
                    "image_url": image_url,
                    "audio_url": audio_url,
                    "audio_duration": audio_duration,
                    "video_url": video_url,
                    "video_duration": video_duration,
                    "document_url": document_url,
                    "document_name": document_name,
                    "document_size": document_size,
                    "reply_to": None,
                    "forwarded": True,
                    "is_read": False,
                    "expires_at": msg['expires_at'],
                    "message_type": "text",
                    "call_duration": "",
                    "is_edited": False,
                    "edited_at": "",
                }
            }
            await self.channel_layer.group_send(f"user_{receiver_id}", payload)
            await self.channel_layer.group_send(f"user_{self.user.id}", payload)

            # ⭐ If receiver is AI bot → generate AI reply
            is_ai = await self._is_ai_user(receiver_id)
            if is_ai and content:
                asyncio.create_task(self._send_ai_reply(receiver_id, content))

        elif action == 'friend_request_accepted':
            sender_id = data.get('sender_id')
            await self.channel_layer.group_send(f"user_{sender_id}", {
                "type": "friend_request_accepted_event",
                "by_user_id": self.user.id,
                "by_username": self.user.username,
                "avatar_url": await self._get_avatar_url(self.user.id),
            })

    # ============================================================
    # AI REPLY LOGIC
    # ============================================================

    @database_sync_to_async
    def _is_ai_user(self, user_id):
        from .models import UserProfile
        return UserProfile.objects.filter(user_id=user_id, is_ai_bot=True).exists()

    @database_sync_to_async
    def _get_ai_context(self, ai_user_id):
        """Get last 6 messages between this user and AI for context."""
        from .models import Message
        from django.db.models import Q
        msgs = Message.objects.filter(
            Q(sender=self.user, receiver_id=ai_user_id) |
            Q(sender_id=ai_user_id, receiver=self.user)
        ).order_by('-timestamp')[:6]
        msgs = list(reversed(msgs))
        history = []
        for m in msgs:
            role = 'user' if m.sender_id == self.user.id else 'assistant'
            content = m.content or ''
            if content:
                history.append({"role": role, "content": content})
        return history

    @database_sync_to_async
    def _call_ai(self, user_message, history):
        from django.conf import settings
        api_key = getattr(settings, 'GROQ_API_KEY', '')
        model = getattr(settings, 'GROQ_MODEL', 'openai/gpt-oss-20b')
        if not api_key:
            return "⚠️ AI service not configured."
        try:
            from groq import Groq
            client = Groq(api_key=api_key)
            messages = [{
                "role": "system",
                "content": (
                    "You are ChatFlow AI, a friendly, helpful assistant built into ChatFlow — "
                    "a real-time chat app. Keep responses short and conversational (like WhatsApp). "
                    "Use emojis occasionally. If user asks about ChatFlow features, help them "
                    "(features: real-time messaging, disappearing messages, voice/video calls, "
                    "message reactions, reply, forward, star, delete, friend requests, "
                    "profile with handle and ChatFlow ID)."
                )
            }]
            for h in (history or [])[-6:]:
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

    @database_sync_to_async
    def _save_ai_message(self, ai_user_id, content):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from .models import Message

        try:
            ai_user = User.objects.get(id=ai_user_id)
        except User.DoesNotExist:
            return None

        m = Message.objects.create(
            sender=ai_user,
            receiver=self.user,
            content=content,
            message_type='text',
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
        return {
            'id': m.id,
            'timestamp': now.strftime('%I:%M %p'),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
        }

    async def _send_ai_reply(self, ai_user_id, user_message):
        """Generate AI reply and send it back after a natural delay."""
        try:
            # Typing indicator from AI
            await self.channel_layer.group_send(f"user_{self.user.id}", {
                "type": "typing_indicator",
                "from": "ChatFlow AI",
            })

            # Natural delay
            await asyncio.sleep(1.5)

            # Get history context
            history = await self._get_ai_context(ai_user_id)

            # Call Groq
            ai_text = await self._call_ai(user_message, history)

            # Save + broadcast
            ai_msg = await self._save_ai_message(ai_user_id, ai_text)
            if not ai_msg:
                return

            payload = {
                "type": "chat_message",
                "message": {
                    "id": ai_msg['id'],
                    "sender": "ChatFlow AI",
                    "sender_id": ai_user_id,
                    "receiver_id": self.user.id,
                    "content": ai_text,
                    "timestamp": ai_msg['timestamp'],
                    "date_iso": ai_msg['date_iso'],
                    "date_display": ai_msg['date_display'],
                    "reply_to": None,
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
                    "expires_at": None,
                    "message_type": "text",
                    "call_duration": "",
                    "is_edited": False,
                    "edited_at": "",
                }
            }
            await self.channel_layer.group_send(f"user_{self.user.id}", payload)

            # Stop typing
            await self.channel_layer.group_send(f"user_{self.user.id}", {
                "type": "stop_typing_indicator",
                "from": "ChatFlow AI",
            })
        except Exception as e:
            print(f"[AI REPLY ERROR] {e}")

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

    async def friend_request_accepted_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "friend_request_accepted",
            "by_user_id": event["by_user_id"],
            "by_username": event["by_username"],
            "avatar_url": event.get("avatar_url", ""),
        }))

    async def new_contact_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "new_contact",
            "contact_id": event["contact_id"],
            "contact_username": event["contact_username"],
            "contact_avatar": event.get("contact_avatar", ""),
        }))

    async def new_friend_request_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "new_friend_request",
            "from_user_id": event["from_user_id"],
            "from_username": event["from_username"],
            "from_avatar": event.get("from_avatar", ""),
        }))

    # ============================================================
    # HELPERS
    # ============================================================

    @database_sync_to_async
    def _get_avatar_url(self, user_id):
        from django.contrib.auth.models import User
        from .models import UserProfile
        try:
            u = User.objects.get(id=user_id)
            p = UserProfile.objects.get(user=u)
            if p.avatar:
                return p.avatar.url
        except Exception:
            pass
        return ""

    @database_sync_to_async
    def save_message(self, receiver_id, content, reply_to_id=None):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from .models import Message, ChatSettings

        receiver = User.objects.get(id=receiver_id)
        reply_to = None
        if reply_to_id:
            try:
                rt = Message.objects.get(id=reply_to_id)
                reply_to = {
                    'id': rt.id,
                    'sender': rt.sender.username,
                    'content': (rt.content or '')[:80],
                }
            except Message.DoesNotExist:
                pass

        expires_at = None
        try:
            setting = ChatSettings.objects.get(user=self.user, other_user=receiver)
            if setting.disappearing_seconds > 0:
                expires_at = timezone.now() + timedelta(seconds=setting.disappearing_seconds)
        except ChatSettings.DoesNotExist:
            pass

        m = Message.objects.create(
            sender=self.user,
            receiver=receiver,
            content=content,
            reply_to_id=reply_to_id if reply_to_id else None,
            expires_at=expires_at,
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
        return {
            'id': m.id,
            'timestamp': now.strftime('%I:%M %p'),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
            'reply_to': reply_to,
            'expires_at': expires_at.isoformat() if expires_at else None,
        }

    @database_sync_to_async
    def save_forwarded_message(self, receiver_id, content, image_url,
                                audio_url, audio_duration, video_url, video_duration,
                                document_url=None, document_name='', document_size=''):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from .models import Message, ChatSettings

        receiver = User.objects.get(id=receiver_id)

        expires_at = None
        try:
            setting = ChatSettings.objects.get(user=self.user, other_user=receiver)
            if setting.disappearing_seconds > 0:
                expires_at = timezone.now() + timedelta(seconds=setting.disappearing_seconds)
        except ChatSettings.DoesNotExist:
            pass

        m = Message.objects.create(
            sender=self.user,
            receiver=receiver,
            content=content or '',
            image_url=image_url,
            audio_url=audio_url,
            audio_duration=audio_duration,
            video_url=video_url,
            video_duration=video_duration,
            document_url=document_url,
            document_name=document_name,
            document_size=document_size,
            forwarded=True,
            expires_at=expires_at,
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
        return {
            'id': m.id,
            'timestamp': now.strftime('%I:%M %p'),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
            'expires_at': expires_at.isoformat() if expires_at else None,
        }

    @database_sync_to_async
    def edit_message(self, msg_id, new_content):
        from django.utils import timezone
        from .models import Message

        try:
            m = Message.objects.get(id=msg_id, sender=self.user)
        except Message.DoesNotExist:
            return False, None

        if m.deleted_for_everyone or m.image_url or m.audio_url or m.video_url or m.document_url:
            return False, None
        if m.message_type and m.message_type != 'text':
            return False, None

        diff = (timezone.now() - m.timestamp).total_seconds()
        if diff > 15 * 60:
            return False, None

        m.content = new_content
        try:
            m.is_edited = True
            m.edited_at = timezone.now()
            m.save(update_fields=['content', 'is_edited', 'edited_at'])
            edited_str = timezone.localtime(m.edited_at).strftime('%I:%M %p')
        except Exception:
            m.save(update_fields=['content'])
            edited_str = timezone.localtime(timezone.now()).strftime('%I:%M %p')

        return True, edited_str

    @database_sync_to_async
    def hard_delete(self, msg_id):
        from .models import Message

        try:
            m = Message.objects.get(id=msg_id, sender=self.user)
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
        except Message.DoesNotExist:
            return False

    @database_sync_to_async
    def mark_read(self, sender_id):
        from .models import Message

        Message.objects.filter(
            sender_id=sender_id,
            receiver=self.user,
            is_read=False,
        ).update(is_read=True)

    @database_sync_to_async
    def add_reaction(self, msg_id, emoji):
        from .models import Message, MessageReaction

        try:
            msg = Message.objects.get(id=msg_id)
        except Message.DoesNotExist:
            return None, None, None

        existing = MessageReaction.objects.filter(
            message=msg, user=self.user, emoji=emoji
        ).first()
        if existing:
            existing.delete()
        else:
            MessageReaction.objects.create(message=msg, user=self.user, emoji=emoji)

        reactions_qs = MessageReaction.objects.filter(message=msg).select_related('user')
        summary = {}
        for r in reactions_qs:
            if r.emoji not in summary:
                summary[r.emoji] = {'emoji': r.emoji, 'count': 0, 'users': [], 'me': False}
            summary[r.emoji]['count'] += 1
            summary[r.emoji]['users'].append(r.user.username)
            if r.user_id == self.user.id:
                summary[r.emoji]['me'] = True

        reactions_list = list(summary.values())
        return reactions_list, msg.sender_id, msg.receiver_id


# ============================================================
# CALL CONSUMER — unchanged
# ============================================================

class CallConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if self.user.is_anonymous:
            await self.close()
            return
        self.call_group = f"call_{self.user.id}"
        await self.channel_layer.group_add(self.call_group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if self.user.is_anonymous:
            return
        call = ACTIVE_CALLS.get(self.user.id)
        if call:
            await self._end_call_cleanup(call, reason='cancelled', by_disconnect=True)
        await self.channel_layer.group_discard(self.call_group, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')

        if action == 'start_call':
            await self._handle_start_call(data)
        elif action == 'accept_call':
            await self._handle_accept_call(data)
        elif action == 'reject_call':
            await self._handle_reject_call(data)
        elif action == 'end_call':
            await self._handle_end_call(data)
        elif action == 'webrtc_signal':
            await self._handle_webrtc_signal(data)

    async def _handle_start_call(self, data):
        receiver_id = int(data.get('receiver_id'))
        call_type = data.get('call_type', 'voice')
        if receiver_id == self.user.id:
            return
        if self.user.id in ACTIVE_CALLS:
            await self.send(text_data=json.dumps({"type": "call_error", "message": "You are already in a call"}))
            return
        receiver_online = receiver_id in ONLINE_USERS
        if receiver_online and receiver_id in ACTIVE_CALLS:
            await self._save_call_log(self.user.id, receiver_id, call_type, 'busy', 0)
            await self.send(text_data=json.dumps({"type": "call_busy", "receiver_id": receiver_id, "call_type": call_type}))
            await self._send_call_chat_message(self.user.id, receiver_id, call_type, 'call_busy', '', True)
            return
        if not receiver_online:
            await self._save_call_log(self.user.id, receiver_id, call_type, 'missed', 0)
            await self.send(text_data=json.dumps({"type": "call_missed_offline", "receiver_id": receiver_id, "call_type": call_type}))
            await self._send_call_chat_message(self.user.id, receiver_id, call_type, 'call_missed', '', True)
            return

        call_state = {
            'caller_id': self.user.id, 'caller_username': self.user.username,
            'receiver_id': receiver_id, 'call_type': call_type,
            'status': 'ringing', 'started_at': None, 'ring_task': None,
        }
        ACTIVE_CALLS[self.user.id] = call_state
        ACTIVE_CALLS[receiver_id] = call_state

        await self.channel_layer.group_send(f"call_{receiver_id}", {
            "type": "incoming_call", "from_user_id": self.user.id,
            "from_username": self.user.username, "call_type": call_type,
        })
        await self.channel_layer.group_send(f"call_{self.user.id}", {
            "type": "call_ringing", "receiver_id": receiver_id, "call_type": call_type,
        })
        task = asyncio.create_task(self._ring_timeout(receiver_id, self.user.id, call_type))
        call_state['ring_task'] = task

    async def _ring_timeout(self, receiver_id, caller_id, call_type):
        try:
            await asyncio.sleep(RING_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        call = ACTIVE_CALLS.get(receiver_id)
        if not call or call.get('status') != 'ringing':
            return
        await self._save_call_log(caller_id, receiver_id, call_type, 'missed', 0)
        await self.channel_layer.group_send(f"call_{caller_id}", {"type": "call_timeout_event", "receiver_id": receiver_id, "call_type": call_type})
        await self.channel_layer.group_send(f"call_{receiver_id}", {"type": "call_cancelled_event", "by_user_id": caller_id})
        await self._send_call_chat_message(caller_id, receiver_id, call_type, 'call_missed', '', True)
        ACTIVE_CALLS.pop(caller_id, None)
        ACTIVE_CALLS.pop(receiver_id, None)

    async def _handle_accept_call(self, data):
        from django.utils import timezone
        caller_id = int(data.get('caller_id'))
        call = ACTIVE_CALLS.get(self.user.id)
        if not call or call.get('status') != 'ringing':
            return
        task = call.get('ring_task')
        if task:
            task.cancel()
        call['status'] = 'active'
        call['started_at'] = timezone.now()
        await self.channel_layer.group_send(f"call_{caller_id}", {"type": "call_accepted_event", "by_user_id": self.user.id, "by_username": self.user.username})
        await self.channel_layer.group_send(f"call_{self.user.id}", {"type": "call_accepted_self", "caller_id": caller_id})

    async def _handle_reject_call(self, data):
        caller_id = int(data.get('caller_id'))
        call = ACTIVE_CALLS.get(self.user.id)
        if not call:
            return
        task = call.get('ring_task')
        if task:
            task.cancel()
        call_type = call.get('call_type', 'voice')
        await self._save_call_log(caller_id, self.user.id, call_type, 'rejected', 0)
        await self.channel_layer.group_send(f"call_{caller_id}", {"type": "call_rejected_event", "by_user_id": self.user.id, "by_username": self.user.username})
        await self._send_call_chat_message(caller_id, self.user.id, call_type, 'call_rejected', '', True)
        ACTIVE_CALLS.pop(caller_id, None)
        ACTIVE_CALLS.pop(self.user.id, None)

    async def _handle_end_call(self, data):
        call = ACTIVE_CALLS.get(self.user.id)
        if not call:
            return
        await self._end_call_cleanup(call, reason='completed')

    async def _end_call_cleanup(self, call, reason='completed', by_disconnect=False):
        from django.utils import timezone
        caller_id = call.get('caller_id')
        receiver_id = call.get('receiver_id')
        call_type = call.get('call_type', 'voice')
        status = call.get('status', 'ringing')
        task = call.get('ring_task')
        if task:
            task.cancel()
        duration_sec = 0
        if status == 'active' and call.get('started_at'):
            duration_sec = int((timezone.now() - call['started_at']).total_seconds())
        final_status = 'answered' if status == 'active' else 'cancelled'
        await self._save_call_log(caller_id, receiver_id, call_type, final_status, duration_sec)
        dur_str = ''
        if duration_sec > 0:
            m = duration_sec // 60
            s = duration_sec % 60
            dur_str = f"{m}:{s:02d}"
        await self.channel_layer.group_send(f"call_{caller_id}", {"type": "call_ended_event", "by_user_id": self.user.id})
        await self.channel_layer.group_send(f"call_{receiver_id}", {"type": "call_ended_event", "by_user_id": self.user.id})
        msg_type = 'call_answered' if status == 'active' else 'call_cancelled'
        await self._send_call_chat_message(caller_id, receiver_id, call_type, msg_type, dur_str, True)
        ACTIVE_CALLS.pop(caller_id, None)
        ACTIVE_CALLS.pop(receiver_id, None)

    async def _handle_webrtc_signal(self, data):
        target_id = int(data.get('target_id'))
        signal = data.get('signal')
        await self.channel_layer.group_send(f"call_{target_id}", {
            "type": "webrtc_signal_event",
            "from_user_id": self.user.id,
            "signal": signal,
        })

    @database_sync_to_async
    def _save_call_log(self, caller_id, receiver_id, call_type, status, duration):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from .models import Call
        try:
            caller = User.objects.get(id=caller_id)
            receiver = User.objects.get(id=receiver_id)
        except User.DoesNotExist:
            return None
        c = Call.objects.create(
            caller=caller, receiver=receiver, call_type=call_type,
            status=status, duration=duration, ended_at=timezone.now(),
        )
        return c.id

    async def _send_call_chat_message(self, sender_id, receiver_id, call_type, message_type, duration_str='', to_both=True):
        msg = await self._create_call_message(sender_id, receiver_id, call_type, message_type, duration_str)
        if not msg:
            return
        payload = {
            "type": "chat_message",
            "message": {
                "id": msg['id'], "sender": msg['sender'],
                "sender_id": sender_id, "receiver_id": receiver_id,
                "content": msg['content'], "timestamp": msg['timestamp'],
                "date_iso": msg['date_iso'], "date_display": msg['date_display'],
                "reply_to": None, "forwarded": False,
                "image_url": None, "audio_url": None, "audio_duration": "",
                "video_url": None, "video_duration": "",
                "document_url": None, "document_name": "", "document_size": "",
                "is_read": False, "expires_at": None,
                "message_type": message_type, "call_duration": duration_str,
                "is_edited": False, "edited_at": "",
            }
        }
        await self.channel_layer.group_send(f"user_{sender_id}", payload)
        if to_both:
            await self.channel_layer.group_send(f"user_{receiver_id}", payload)

    @database_sync_to_async
    def _create_call_message(self, sender_id, receiver_id, call_type, message_type, duration_str):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from .models import Message
        try:
            sender = User.objects.get(id=sender_id)
            receiver = User.objects.get(id=receiver_id)
        except User.DoesNotExist:
            return None
        label = 'video' if call_type == 'video' else 'voice'
        if message_type == 'call_missed':
            content = f"📞 Missed {label} call"
            if duration_str:
                content += f" · {duration_str}"
        elif message_type == 'call_rejected':
            content = f"📞 {label.capitalize()} call declined"
        elif message_type == 'call_busy':
            content = f"📞 {label.capitalize()} call — user busy"
        elif message_type == 'call_answered':
            content = f"📞 {label.capitalize()} call · {duration_str}"
        elif message_type == 'call_cancelled':
            content = f"📞 {label.capitalize()} call cancelled"
        elif message_type == 'call_outgoing':
            content = f"📞 Outgoing {label} call"
        else:
            content = f"📞 {label.capitalize()} call"
        m = Message.objects.create(
            sender=sender, receiver=receiver, content=content,
            message_type=message_type, call_duration=duration_str or '',
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
        return {
            'id': m.id, 'sender': sender.username, 'content': content,
            'timestamp': now.strftime('%I:%M %p'),
            'date_iso': msg_date.strftime('%Y-%m-%d'),
            'date_display': date_display,
        }

    async def incoming_call(self, event):
        await self.send(text_data=json.dumps({"type": "incoming_call", "from_user_id": event["from_user_id"], "from_username": event["from_username"], "call_type": event["call_type"]}))

    async def call_ringing(self, event):
        await self.send(text_data=json.dumps({"type": "call_ringing", "receiver_id": event["receiver_id"], "call_type": event["call_type"]}))

    async def call_accepted_event(self, event):
        await self.send(text_data=json.dumps({"type": "call_accepted", "by_user_id": event["by_user_id"], "by_username": event.get("by_username", "")}))

    async def call_accepted_self(self, event):
        await self.send(text_data=json.dumps({"type": "call_accepted_self", "caller_id": event["caller_id"]}))

    async def call_rejected_event(self, event):
        await self.send(text_data=json.dumps({"type": "call_rejected", "by_user_id": event["by_user_id"], "by_username": event.get("by_username", "")}))

    async def call_ended_event(self, event):
        await self.send(text_data=json.dumps({"type": "call_ended", "by_user_id": event["by_user_id"]}))

    async def call_timeout_event(self, event):
        await self.send(text_data=json.dumps({"type": "call_timeout", "receiver_id": event["receiver_id"], "call_type": event["call_type"]}))

    async def call_cancelled_event(self, event):
        await self.send(text_data=json.dumps({"type": "call_cancelled", "by_user_id": event["by_user_id"]}))

    async def webrtc_signal_event(self, event):
        await self.send(text_data=json.dumps({"type": "webrtc_signal", "from_user_id": event["from_user_id"], "signal": event["signal"]}))