"""URL configuration for the backend project."""

from django.urls import include, path

urlpatterns = [
    path("api/", include("core.urls")),
    path("api/ingest/", include("integrations.urls")),
    path("api/health/", include("health.urls")),
    path("api/permissions/", include("authorization.urls")),
]
