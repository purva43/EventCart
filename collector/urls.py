from django.urls import path

from . import views

urlpatterns = [
    path("", views.public_home, name="home"),
    path("register/", views.user_register, name="user_register"),
    path("login/", views.user_login, name="user_login"),
    path("logout/", views.user_logout, name="user_logout"),
    path("admin/login/", views.admin_login_view, name="admin_login"),
    path("admin/logout/", views.admin_logout_view, name="admin_logout"),
    path("admin/dashboard/", views.dashboard, name="dashboard"),
    path("folders/", views.list_folders_page, name="folders"),
    path("search/", views.search_events_page, name="search_events"),
    path("folders/<int:fid>/", views.folder_events_page, name="folder_events"),
    path("event/<str:eid>/", views.event_page, name="event_detail"),
    path("html/<path:filename>/", views.serve_html_page, name="serve_html_page"),
    path("api/v1/health/", views.api_health, name="api_v1_health"),
    path("api/v1/areas/", views.api_areas, name="api_v1_areas"),
    path("api/v1/folders/", views.api_list_folders, name="api_v1_folders"),
    path("api/v1/folders/<int:fid>/events/", views.api_folder_events, name="api_v1_folder_events"),
    path("api/v1/folders/<int:fid>/nearby/", views.api_folder_nearby_events, name="api_v1_folder_nearby_events"),
    path("api/v1/nearby-selected/", views.api_nearby_selected_events, name="api_v1_nearby_selected_events"),
    path("api/v1/events/<str:eid>/interest/", views.api_mark_event_interest, name="api_v1_mark_event_interest"),
]
