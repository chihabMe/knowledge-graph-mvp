from collections.abc import Mapping

from django.conf import settings
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

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
from retrieval.services import answer_query


def _invalid_chat_request_payload(data) -> dict:
    messages = data.get("messages") if isinstance(data, Mapping) else None
    if isinstance(messages, list) and len(messages) > MAX_OPEN_WEBUI_MESSAGES:
        return {
            "error": {
                "message": "This conversation is too long. Start a new chat and try again.",
                "type": "invalid_request_error",
                "code": "conversation_too_long",
            }
        }
    return {
        "error": {
            "message": "The chat request could not be processed.",
            "type": "invalid_request_error",
            "code": "invalid_request",
        }
    }


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
            return Response(
                _invalid_chat_request_payload(request.data),
                status=status.HTTP_400_BAD_REQUEST,
                headers={"Cache-Control": "no-store"},
            )
        result = answer_query(serializer.validated_data["question"], request.user.email)
        if serializer.validated_data["stream"]:
            response = StreamingHttpResponse(
                iter(
                    build_buffered_chat_completion_events(
                        result,
                        serializer.validated_data["model"],
                    )
                ),
                content_type="text/event-stream",
            )
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response
        return Response(
            build_chat_completion_payload(
                result,
                serializer.validated_data["model"],
            )
        )
