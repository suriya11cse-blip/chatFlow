# chat_ai/admin.py
from django.contrib import admin
from .models import AIConversation, AIMessage, AISummary, AIGeneratedImage, AISettings


@admin.register(AIConversation)
class AIConversationAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'title', 'created_at', 'updated_at']
    search_fields = ['user__username', 'title']


@admin.register(AIMessage)
class AIMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'conversation', 'role', 'content_preview', 'created_at']
    search_fields = ['content']

    def content_preview(self, obj):
        return obj.content[:60]
    content_preview.short_description = 'Content'


@admin.register(AISummary)
class AISummaryAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'chat_with', 'room', 'message_count', 'created_at']


@admin.register(AIGeneratedImage)
class AIGeneratedImageAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'prompt_preview', 'created_at']

    def prompt_preview(self, obj):
        return obj.prompt[:60]
    prompt_preview.short_description = 'Prompt'


@admin.register(AISettings)
class AISettingsAdmin(admin.ModelAdmin):
    list_display = ['user', 'auto_translate', 'smart_reply_enabled', 'group_assistant_enabled']