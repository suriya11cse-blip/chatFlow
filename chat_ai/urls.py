# chat_ai/urls.py
from django.urls import path
from . import views


urlpatterns = [
    # AI Chat
    path('chat/', views.ai_chat, name='ai_chat'),
    path('conversations/', views.ai_conversations_list, name='ai_conversations'),
    path('conversations/<int:conv_id>/', views.ai_conversation_detail, name='ai_conversation_detail'),

    # Summarize
    path('summarize/', views.summarize_chat, name='ai_summarize'),

    # Smart Reply
    path('smart-replies/', views.get_smart_replies, name='ai_smart_replies'),

    # Translate
    path('translate/', views.translate_message, name='ai_translate'),

    # Image Generation
    path('generate-image/', views.generate_image, name='ai_generate_image'),

    # Voice
    path('voice-to-text/', views.voice_to_text, name='ai_voice_to_text'),
    path('text-to-voice/', views.text_to_voice, name='ai_text_to_voice'),

    # Group Bot
    path('group-bot/', views.group_bot, name='ai_group_bot'),

    # Settings
    path('settings/', views.get_ai_settings, name='ai_settings'),
    path('settings/update/', views.update_ai_settings, name='ai_settings_update'),
]