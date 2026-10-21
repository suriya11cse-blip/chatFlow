# chat_ai/models.py
"""
ChatFlow — AI Features Models
App: chat_ai
"""
from django.db import models
from django.contrib.auth.models import User


# ============================================================
# AI CONVERSATION
# ============================================================
class AIConversation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ai_conversations')
    title = models.CharField(max_length=255, default='New Chat')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.user.username}: {self.title[:40]}"


# ============================================================
# AI MESSAGE
# ============================================================
class AIMessage(models.Model):
    ROLE_CHOICES = (
        ('user', 'User'),
        ('assistant', 'Assistant'),
        ('system', 'System'),
    )

    conversation = models.ForeignKey(AIConversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    audio_file = models.FileField(upload_to='ai_audio/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"


# ============================================================
# AI SUMMARY
# ============================================================
class AISummary(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ai_summaries')
    chat_with = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='summaries_received',
        null=True, blank=True
    )
    room = models.ForeignKey('chat.ChatRoom', on_delete=models.CASCADE, null=True, blank=True)
    summary_text = models.TextField()
    message_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username}: {self.summary_text[:40]}"


# ============================================================
# AI GENERATED IMAGE
# ============================================================
class AIGeneratedImage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ai_images')
    prompt = models.TextField()
    image = models.ImageField(upload_to='ai_images/')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username}: {self.prompt[:40]}"


# ============================================================
# AI SETTINGS
# ============================================================
class AISettings(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='ai_settings')
    auto_translate = models.BooleanField(default=False)
    translate_to = models.CharField(max_length=10, default='ta-IN')
    smart_reply_enabled = models.BooleanField(default=True)
    voice_reply_enabled = models.BooleanField(default=False)
    group_assistant_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AI Settings — {self.user.username}"