from django.urls import path

from integrations.google_session_oauth_views import (
    GoogleSessionOAuthCallbackView,
    GoogleSessionOAuthStartView,
)

urlpatterns = [
    path("start", GoogleSessionOAuthStartView.as_view(), name="google-session-oauth-start"),
    path(
        "callback",
        GoogleSessionOAuthCallbackView.as_view(),
        name="google-session-oauth-callback",
    ),
]
