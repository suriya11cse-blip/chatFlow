from django.db import models
from django.contrib.auth.models import User


class Message(models.Model):
    MESSAGE_TYPES = (
        ('text', 'Text'),
        ('call_missed', 'Missed Call'),
        ('call_outgoing', 'Outgoing Call'),
        ('call_incoming', 'Incoming Call'),
        ('call_answered', 'Answered Call'),
        ('call_rejected', 'Rejected Call'),
        ('call_cancelled', 'Cancelled Call'),
        ('call_busy', 'Busy Call'),
    )

    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages', null=True, blank=True)
    content = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)
    deleted_for = models.ManyToManyField(User, related_name='deleted_messages', blank=True)
    deleted_for_everyone = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    is_starred = models.BooleanField(default=False)

    # Edit tracking
    is_edited = models.BooleanField(default=False)
    edited_at = models.DateTimeField(null=True, blank=True)

    is_pinned = models.BooleanField(default=False, db_index=True)


    image_url = models.URLField(blank=True, null=True)
    audio_url = models.URLField(blank=True, null=True)
    audio_duration = models.CharField(max_length=10, blank=True, default='')
    video_url = models.URLField(blank=True, null=True)
    video_duration = models.CharField(max_length=10, blank=True, default='')

    document_url = models.URLField(blank=True, null=True)
    document_name = models.CharField(max_length=255, blank=True, default='')
    document_size = models.CharField(max_length=20, blank=True, default='')

    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replies')
    forwarded = models.BooleanField(default=False)

    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPES, default='text')
    call_duration = models.CharField(max_length=10, blank=True, default='')
    room = models.ForeignKey(
        'ChatRoom', on_delete=models.CASCADE,
        null=True, blank=True, related_name='messages'
    )
    mentioned_users = models.ManyToManyField(
        User, related_name='mentioned_in_messages', blank=True
    )

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.sender} -> {self.receiver}: {self.content[:20]}"


class MessageReaction(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    emoji = models.CharField(max_length=10)

    class Meta:
        unique_together = ('message', 'user', 'emoji')

    def __str__(self):
        return f"{self.user.username} {self.emoji} -> msg {self.message.id}"


class UserProfile(models.Model):
    FONT_SIZES = (
        ('small', 'Small'),
        ('medium', 'Medium'),
        ('large', 'Large'),
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.CharField(max_length=200, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    handle = models.CharField(max_length=30, unique=True, blank=True, null=True)
    chatflow_id = models.CharField(max_length=12, unique=True, blank=True, null=True, db_index=True)
    is_ai_bot = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)

    # ⭐ TIER 2: User preferences
    show_read_receipts = models.BooleanField(default=True)
    font_size = models.CharField(max_length=10, choices=FONT_SIZES, default='medium')

    def __str__(self):
        return f"{self.user.username} profile"


class ChatSettings(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_settings')
    other_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='other_chat_settings')
    disappearing_seconds = models.IntegerField(default=0)

    # ⭐ TIER 2: Chat wallpaper
    wallpaper = models.CharField(max_length=255, blank=True, default='')

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'other_user')

    def __str__(self):
        return f"{self.user.username} <-> {self.other_user.username}: {self.disappearing_seconds}s"


class FriendRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
    )

    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_requests')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_requests')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('sender', 'receiver')

    def __str__(self):
        return f"{self.sender.username} -> {self.receiver.username} ({self.status})"


class ChatContact(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_contacts')
    contact = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contact_of')
    created_at = models.DateTimeField(auto_now_add=True)

    is_muted = models.BooleanField(default=False)
    is_pinned = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)

    # ⭐ TIER 2: Per-chat notification sound
    sound_enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = ('user', 'contact')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} → {self.contact.username}"


class Call(models.Model):
    CALL_TYPES = (
        ('voice', 'Voice Call'),
        ('video', 'Video Call'),
    )
    CALL_STATUS = (
        ('missed', 'Missed'),
        ('answered', 'Answered'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
        ('busy', 'Busy'),
        ('timeout', 'Timeout'),
    )

    caller = models.ForeignKey(User, on_delete=models.CASCADE, related_name='outgoing_calls')
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='incoming_calls')
    call_type = models.CharField(max_length=10, choices=CALL_TYPES, default='voice')
    status = models.CharField(max_length=10, choices=CALL_STATUS, default='missed')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration = models.IntegerField(default=0)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.caller} → {self.receiver} ({self.call_type}, {self.status})"
    


# ============================================================
# ⭐ TIER 5: GROUP CHAT
# ============================================================

class ChatRoom(models.Model):
    """
    Group chat room.
    Members are tracked via ChatRoomMember (M2M with extra fields).
    """
    ROOM_TYPES = (
        ('group', 'Group'),
    )

    name = models.CharField(max_length=100)
    description = models.CharField(max_length=500, blank=True, default='')
    avatar = models.ImageField(upload_to='group_avatars/', blank=True, null=True)
    room_type = models.CharField(max_length=10, choices=ROOM_TYPES, default='group')
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_rooms'
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.name} (group #{self.id})"

    @property
    def member_count(self):
        return self.members.count()


class ChatRoomMember(models.Model):
    """
    Membership of a user in a group chat.
    Has per-user flags: role, muted, pinned, archived.
    """
    ROLES = (
        ('admin', 'Admin'),
        ('member', 'Member'),
    )

    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='group_memberships')
    role = models.CharField(max_length=10, choices=ROLES, default='member')
    joined_at = models.DateTimeField(auto_now_add=True)
    last_read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Per-user per-group flags
    is_muted = models.BooleanField(default=False)
    is_pinned = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)

    class Meta:
        unique_together = ('room', 'user')
        ordering = ['-joined_at']

    def __str__(self):
        return f"{self.user.username} in {self.room.name} ({self.role})"


# ============================================================
# ⭐ Message model patch — add 'room' + 'mentioned_users'
# (Idhu unga existing Message model la add pannanum — see instructions)
# ============================================================

# ============================================================
# ⭐ TIER 3: STATUS / STORIES
# ============================================================

class Status(models.Model):
    """
    WhatsApp-style status (24-hour expiry).
    Can be text, image, or video.
    """
    STATUS_TYPES = (
        ('text', 'Text'),
        ('image', 'Image'),
        ('video', 'Video'),
    )

    BACKGROUND_COLORS = [
        ('#25D366', 'Green'),
        ('#128C7E', 'Teal'),
        ('#075E54', 'Dark Green'),
        ('#1E88E5', 'Blue'),
        ('#E91E63', 'Pink'),
        ('#F57C00', 'Orange'),
        ('#8E24AA', 'Purple'),
        ('#43A047', 'Light Green'),
        ('#546E7A', 'Grey'),
        ('#1A1A2E', 'Dark'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='statuses')
    status_type = models.CharField(max_length=10, choices=STATUS_TYPES, default='text')

    # Text status
    content = models.TextField(blank=True, default='')
    background_color = models.CharField(max_length=20, blank=True, default='#25D366')
    font_style = models.CharField(max_length=20, blank=True, default='normal')

    # Image/Video status
    media_url = models.URLField(blank=True, null=True)
    caption = models.TextField(blank=True, default='')

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.status_type} ({self.created_at})"

    @property
    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expires_at


class StatusView(models.Model):
    """Track who viewed a status."""
    status = models.ForeignKey(Status, on_delete=models.CASCADE, related_name='views')
    viewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='status_views')
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('status', 'viewer')
        ordering = ['-viewed_at']

    def __str__(self):
        return f"{self.viewer.username} viewed status {self.status.id}"
    