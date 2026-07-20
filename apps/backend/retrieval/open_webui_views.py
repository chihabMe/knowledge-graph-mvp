import logging
from collections.abc import Mapping

from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from integrations.drive.onboarding import (
    NOT_CONNECTED,
    READY,
    REAUTHORIZATION_REQUIRED,
    SYNCING,
    connection_state,
    session_onboarding_url,
)
from retrieval.open_webui import (
    build_buffered_chat_completion_events,
    build_chat_completion_payload,
)
from retrieval.open_webui_auth import (
    OpenWebUIServiceAuthentication,
    OpenWebUIUserAuthentication,
)
from retrieval.serializers import (
    MAX_OPEN_WEBUI_MESSAGES,
    OpenWebUIChatCompletionRequestSerializer,
    OpenWebUIModelListSerializer,
)
from retrieval.services import QueryResult, answer_query

logger = logging.getLogger(__name__)


def _onboarding_result(user_email: str) -> QueryResult | None:
    if settings.GOOGLE_PERMISSION_AUTHORITY != "per_user_oauth":
        return None
    try:
        state = connection_state(user_email=user_email).state
    except Exception as exc:
        logger.warning(
            "Drive onboarding state lookup failed closed (%s.%s).",
            type(exc).__module__,
            type(exc).__name__,
        )
        state = "temporarily_unavailable"
    if state == READY:
        return None
    if state == NOT_CONNECTED:
        answer = (
            "Connect Google Drive before asking questions about your documents: "
            f"[Connect Google Drive](<{session_onboarding_url()}>)"
        )
        reason = "drive_authorization_required"
    elif state == REAUTHORIZATION_REQUIRED:
        answer = (
            "Your Google Drive connection needs to be renewed: "
            f"[Reconnect Google Drive](<{session_onboarding_url()}>)"
        )
        reason = "drive_reauthorization_required"
    elif state == SYNCING:
        answer = "Your Google Drive permissions are synchronizing. Please try again shortly."
        reason = "drive_visibility_sync_pending"
    else:
        answer = "Google Drive is temporarily unavailable. Please try again shortly."
        reason = "drive_temporarily_unavailable"
    return QueryResult(answer=answer, citations=(), refused=True, reason=reason)


def _completion_response(result: QueryResult, *, model: str, stream: bool):
    if stream:
        response = StreamingHttpResponse(
            iter(build_buffered_chat_completion_events(result, model)),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
    return Response(build_chat_completion_payload(result, model))


class OpenWebUIModelsView(APIView):
    authentication_classes = [OpenWebUIServiceAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "open-webui-models"

    def get(self, request):
        serializer = OpenWebUIModelListSerializer(
            {
                "object": "list",
                "data": [
                    {
                        "id": settings.OPEN_WEBUI_MODEL_ID,
                        "object": "model",
                        "created": 0,
                        "owned_by": "knowledge-graph-mvp",
                    }
                ],
            }
        )
        return Response(serializer.data)


class OpenWebUIChatCompletionsView(APIView):
    authentication_classes = [OpenWebUIUserAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "open-webui-chat"

    def post(self, request):
        serializer = OpenWebUIChatCompletionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            messages = request.data.get("messages") if isinstance(request.data, Mapping) else None
            conversation_too_long = (
                isinstance(messages, list) and len(messages) > MAX_OPEN_WEBUI_MESSAGES
            )
            error = (
                {
                    "message": "This conversation is too long. Start a new chat and try again.",
                    "type": "invalid_request_error",
                    "code": "conversation_too_long",
                }
                if conversation_too_long
                else {
                    "message": "The chat request could not be processed.",
                    "type": "invalid_request_error",
                    "code": "invalid_request",
                }
            )
            return Response(
                {"error": error},
                status=status.HTTP_400_BAD_REQUEST,
                headers={"Cache-Control": "no-store"},
            )
        result = _onboarding_result(request.user.email) or answer_query(
            serializer.validated_data["question"], request.user.email
        )
        return _completion_response(
            result,
            model=serializer.validated_data["model"],
            stream=serializer.validated_data["stream"],
        )
