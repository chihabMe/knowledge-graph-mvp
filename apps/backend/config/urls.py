"""URL configuration for the backend project."""

from django.urls import include, path

urlpatterns = [
    path("api/", include("core.urls")),
    path("api/session/google/", include("integrations.google_session_oauth_urls")),
    path("api/drive/", include("integrations.drive_oauth_urls")),
    path("api/ingest/", include("integrations.urls")),
    path("api/health/", include("health.urls")),
    path("api/permissions/", include("authorization.urls")),
    path("api/query/", include("retrieval.urls")),
    path("v1/", include("retrieval.open_webui_urls")),
]
