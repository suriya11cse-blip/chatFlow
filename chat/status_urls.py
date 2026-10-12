from django.urls import path
from . import status_views

urlpatterns = [
    path('', status_views.list_statuses, name='status_list'),
    path('create/', status_views.create_status, name='status_create'),
    path('<int:status_id>/view/', status_views.view_status, name='status_view'),
    path('<int:status_id>/viewers/', status_views.get_status_viewers, name='status_viewers'),
    path('<int:status_id>/info/', status_views.get_status_info, name='status_info'),
    path('<int:status_id>/delete/', status_views.delete_status, name='status_delete'),
    path('delete-all/', status_views.delete_all_my_statuses, name='status_delete_all'),
    path('<int:status_id>/reply/', status_views.reply_to_status, name='status_reply'),
]