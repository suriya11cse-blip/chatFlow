from django.urls import path
from . import views

urlpatterns = [
    # ===== Auth =====
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),

    # ===== Messages =====
    path('messages/<int:user_id>/', views.get_messages, name='get_messages'),
    path('delete-for-me/<int:msg_id>/', views.delete_for_me, name='delete_for_me'),
    path('delete-for-everyone/<int:msg_id>/', views.delete_for_everyone, name='delete_for_everyone'),
    path('clear-chat/<int:user_id>/', views.clear_chat, name='clear_chat'),
    path('toggle-star/<int:msg_id>/', views.toggle_star, name='toggle_star'),

    # ===== Disappearing Messages =====
    path('set-disappearing/<int:user_id>/', views.set_disappearing, name='set_disappearing'),
    path('get-disappearing/<int:user_id>/', views.get_disappearing, name='get_disappearing'),

    # ===== Friend Requests =====
    path('send-friend-request/', views.send_friend_request, name='send_friend_request'),
    path('accept-friend-request/<int:request_id>/', views.accept_friend_request, name='accept_friend_request'),
    path('reject-friend-request/<int:request_id>/', views.reject_friend_request, name='reject_friend_request'),
    path('get-friend-requests/', views.get_friend_requests, name='get_friend_requests'),
    path('search-handle/', views.search_handle, name='search_handle'),

    # ===== Contact (WhatsApp-style direct) =====
    path('contact-user/', views.contact_user, name='contact_user'),

    # ===== File Uploads =====
    path('upload-image/', views.upload_image, name='upload_image'),
    path('upload-audio/', views.upload_audio, name='upload_audio'),
    path('upload-video/', views.upload_video, name='upload_video'),
    path('upload-document/', views.upload_document, name='upload_document'),

    # ===== Profile =====
    path('profile/', views.profile_view, name='profile'),
    path('user/<int:user_id>/', views.user_profile_view, name='user_profile'),
    path('update-profile/', views.update_profile, name='update_profile'),
    path('update-handle/', views.update_handle, name='update_handle'),
    path('upload-avatar/', views.upload_avatar, name='upload_avatar'),
    path('delete-avatar/', views.delete_avatar, name='delete_avatar'),
    path('change-password/', views.change_password, name='change_password'),
    path('update-email/', views.update_email, name='update_email'),

    # ===== Calls =====
    path('call-history/<int:user_id>/', views.call_history, name='call_history'),
]