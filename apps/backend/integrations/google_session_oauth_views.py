"""Public entry points for the identity-only Django session bootstrap."""

from django.contrib.auth import login
from django.shortcuts import redirect, render
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from integrations.drive.google_session_oauth import (
    GoogleSessionOAuthError,
    begin_session_authorization,
    complete_session_authorization,
)


def _error_status(exc: GoogleSessionOAuthError) -> int:
    return {
        "session_oauth_not_configured": 409,
        "identity_not_allowed": 403,
        "identity_conflict": 403,
        "authorization_exchange_failed": 502,
    }.get(exc.code, 400)


def _error_page(request, exc: GoogleSessionOAuthError):
    response = render(
        request,
        "integrations/google_session_oauth_result.html",
        {"authenticated": False},
        status=_error_status(exc),
    )
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


class GoogleSessionOAuthStartView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "google-session-start"

    def get(self, request):
        try:
            authorization_url = begin_session_authorization(session=request.session)
        except GoogleSessionOAuthError as exc:
            return _error_page(request, exc)
        response = redirect(authorization_url)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class GoogleSessionOAuthCallbackView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "google-session-callback"

    def get(self, request):
        try:
            user = complete_session_authorization(
                session=request.session,
                state=request.query_params.get("state"),
                code=request.query_params.get("code"),
                provider_error=bool(request.query_params.get("error")),
            )
        except GoogleSessionOAuthError as exc:
            return _error_page(request, exc)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        response = redirect("drive-oauth-start")
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response
