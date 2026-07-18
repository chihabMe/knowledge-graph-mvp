"""Minimal authenticated endpoints for the per-user Drive OAuth boundary."""

import logging

from django.shortcuts import redirect, render
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from integrations.drive.user_oauth import (
    UserDriveOAuthError,
    authorization_status,
    begin_authorization,
    complete_authorization,
    disconnect_authorization,
)
from integrations.drive.user_visibility_sync import (
    UserVisibilitySyncError,
    queue_user_visibility_sync,
)
from integrations.tasks import run_user_visibility_sync
from retrieval.identity import TrustedIdentityUnavailable, trusted_user_email

logger = logging.getLogger(__name__)


def _trusted_email(request) -> str:
    try:
        return trusted_user_email(request.user)
    except TrustedIdentityUnavailable as exc:
        raise UserDriveOAuthError("identity_unavailable") from exc


def _status_for_error(exc: UserDriveOAuthError) -> int:
    return {
        "identity_unavailable": status.HTTP_403_FORBIDDEN,
        "identity_not_allowed": status.HTTP_403_FORBIDDEN,
        "identity_mismatch": status.HTTP_403_FORBIDDEN,
        "oauth_not_configured": status.HTTP_409_CONFLICT,
        "connection_unavailable": status.HTTP_409_CONFLICT,
        "connection_changed": status.HTTP_409_CONFLICT,
        "authorization_exchange_failed": status.HTTP_502_BAD_GATEWAY,
        "credential_storage_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    }.get(exc.code, status.HTTP_400_BAD_REQUEST)


def _error_response(exc: UserDriveOAuthError) -> Response:
    return Response(
        {"detail": "Google Drive authorization could not be completed."},
        status=_status_for_error(exc),
        headers={"Cache-Control": "no-store"},
    )


class DriveOAuthStartView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-oauth-start"

    def get(self, request):
        try:
            authorization_url = begin_authorization(
                session=request.session,
                user_email=_trusted_email(request),
            )
        except UserDriveOAuthError as exc:
            return _error_response(exc)
        response = redirect(authorization_url)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class DriveOAuthCallbackView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-oauth-callback"

    def get(self, request):
        sync_queued = False
        try:
            user_email = _trusted_email(request)
            complete_authorization(
                session=request.session,
                user_email=user_email,
                state=request.query_params.get("state"),
                authorization_code=request.query_params.get("code"),
                provider_error=bool(
                    request.query_params.get("error") or not request.query_params.get("code")
                ),
            )
        except UserDriveOAuthError as exc:
            response = render(
                request,
                "integrations/drive_oauth_result.html",
                {"connected": False},
                status=_status_for_error(exc),
            )
        else:
            try:
                queue_user_visibility_sync(
                    user_email=user_email,
                    dispatch=run_user_visibility_sync.delay,
                )
            except Exception as exc:
                # The completed authorization remains valid. A durable queued
                # run or the periodic scheduler can retry without making the
                # user repeat consent. Never expose provider/broker details.
                logger.warning(
                    "Immediate user visibility synchronization dispatch failed (%s).",
                    exc.__class__.__name__,
                )
            else:
                sync_queued = True
            response = render(
                request,
                "integrations/drive_oauth_result.html",
                {"connected": True, "sync_queued": sync_queued},
            )
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class DriveOAuthStatusView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-oauth-status"

    def get(self, request):
        try:
            oauth_status = authorization_status(user_email=_trusted_email(request))
        except UserDriveOAuthError as exc:
            return _error_response(exc)
        return Response(oauth_status.as_payload(), headers={"Cache-Control": "no-store"})


class DriveOAuthDisconnectView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-oauth-disconnect"

    def post(self, request):
        try:
            disconnect_authorization(user_email=_trusted_email(request))
        except UserDriveOAuthError as exc:
            return _error_response(exc)
        return Response(
            {"connected": False, "status": "disconnected"},
            headers={"Cache-Control": "no-store"},
        )


class DriveVisibilitySyncView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-visibility-sync"

    def post(self, request):
        if request.data:
            return Response(
                {"detail": "A visibility refresh could not be queued."},
                status=status.HTTP_400_BAD_REQUEST,
                headers={"Cache-Control": "no-store"},
            )
        try:
            run = queue_user_visibility_sync(
                user_email=_trusted_email(request),
                dispatch=run_user_visibility_sync.delay,
            )
        except (UserDriveOAuthError, UserVisibilitySyncError):
            return Response(
                {"detail": "A visibility refresh could not be queued."},
                status=status.HTTP_409_CONFLICT,
                headers={"Cache-Control": "no-store"},
            )
        return Response(
            {"run_id": run.pk, "status": run.status},
            status=status.HTTP_202_ACCEPTED,
            headers={"Cache-Control": "no-store"},
        )
