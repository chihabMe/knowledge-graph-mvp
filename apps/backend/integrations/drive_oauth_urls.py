from django.urls import path

from integrations.drive_oauth_views import (
    DriveOAuthCallbackView,
    DriveOAuthDisconnectView,
    DriveOAuthStartView,
    DriveOAuthStatusView,
    DriveVisibilitySyncView,
)

urlpatterns = [
    path("oauth/start", DriveOAuthStartView.as_view(), name="drive-oauth-start"),
    path("oauth/callback", DriveOAuthCallbackView.as_view(), name="drive-oauth-callback"),
    path("oauth/status", DriveOAuthStatusView.as_view(), name="drive-oauth-status"),
    path(
        "oauth/disconnect",
        DriveOAuthDisconnectView.as_view(),
        name="drive-oauth-disconnect",
    ),
    path(
        "visibility/sync",
        DriveVisibilitySyncView.as_view(),
        name="drive-visibility-sync",
    ),
]
