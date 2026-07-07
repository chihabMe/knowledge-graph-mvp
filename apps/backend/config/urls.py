"""URL configuration for the backend project."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("api/", include("core.urls")),
    path("api/ingest/", include("integrations.urls")),
    path("api/health/", include("health.urls")),
    path("admin/", admin.site.urls),
]
