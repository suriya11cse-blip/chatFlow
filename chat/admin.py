from django.contrib import admin
from .models import Message, MessageReaction, UserProfile, Call, ChatSettings


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'sender', 'receiver', 'content', 'timestamp', 'is_read', 'is_starred')
    list_filter = ('is_read', 'is_starred', 'deleted_for_everyone')
    search_fields = ('content', 'sender__username', 'receiver__username')


@admin.register(MessageReaction)
class MessageReactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'message', 'user', 'emoji')
    list_filter = ('emoji',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'bio', 'phone')


@admin.register(ChatSettings)
class ChatSettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'other_user', 'disappearing_seconds', 'updated_at')


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = ('id', 'caller', 'receiver', 'call_type', 'status', 'started_at', 'duration')
    list_filter = ('call_type', 'status')