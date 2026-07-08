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
    path("api/areas/", views.api_areas, name="api_areas"),
    path("api/folders/", views.api_list_folders, name="api_folders"),
    path("api/folders/<int:fid>/events/", views.api_folder_events, name="api_folder_events"),
    path("api/folders/<int:fid>/nearby/", views.api_folder_nearby_events, name="api_folder_nearby_events"),
    path("api/nearby-selected/", views.api_nearby_selected_events, name="api_nearby_selected_events"),
    path("api/events/<str:eid>/interest/", views.api_mark_event_interest, name="api_mark_event_interest"),
]
