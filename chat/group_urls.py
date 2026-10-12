# chat/group_urls.py
"""
Group Chat URLs.
Included from chat/urls.py with prefix 'groups/'.
"""
from django.urls import path
from . import group_views

urlpatterns = [
    # ===== Group CRUD =====
    path('', group_views.list_groups, name='group_list'),
    path('create/', group_views.create_group, name='group_create'),
    path('<int:room_id>/info/', group_views.group_info, name='group_info'),
    path('<int:room_id>/update/', group_views.update_group, name='group_update'),
    path('<int:room_id>/delete/', group_views.delete_group, name='group_delete'),

    # ===== Members =====
    path('<int:room_id>/add-members/', group_views.add_members, name='group_add_members'),
    path('<int:room_id>/remove-member/<int:user_id>/', group_views.remove_member, name='group_remove_member'),
    path('<int:room_id>/make-admin/<int:user_id>/', group_views.make_admin, name='group_make_admin'),
    path('<int:room_id>/remove-admin/<int:user_id>/', group_views.remove_admin, name='group_remove_admin'),
    path('<int:room_id>/leave/', group_views.leave_group, name='group_leave'),

    # ===== Messages =====
    path('<int:room_id>/messages/', group_views.get_group_messages, name='group_messages'),
    path('<int:room_id>/send/', group_views.send_group_message, name='group_send'),

    # ===== Flags (mute/pin/archive) =====
    path('<int:room_id>/toggle-flag/', group_views.toggle_group_flag, name='group_toggle_flag'),

    # ⭐ Mark as read (reset unread count)
    path('<int:room_id>/mark-read/', group_views.mark_group_read, name='group_mark_read'),
]