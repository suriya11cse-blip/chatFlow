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
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.CharField(max_length=200, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    handle = models.CharField(max_length=30, unique=True, blank=True, null=True)
    chatflow_id = models.CharField(max_length=12, unique=True, blank=True, null=True, db_index=True)
    is_ai_bot = models.BooleanField(default=False)   # ⭐ NEW

    def __str__(self):
        return f"{self.user.username} profile"


class ChatSettings(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_settings')
    other_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='other_chat_settings')
    disappearing_seconds = models.IntegerField(default=0)
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
    """
    WhatsApp-style direct contact.
    When user A searches & opens chat with user B (via handle / ID / phone),
    a ChatContact is created for both directions so both see each other
    in their sidebar without needing a friend request.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_contacts')
    contact = models.ForeignKey(User, on_delete=models.CASCADE, related_name='contact_of')
    created_at = models.DateTimeField(auto_now_add=True)

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