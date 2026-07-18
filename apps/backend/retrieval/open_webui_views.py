from django.conf import settings
from django.http import StreamingHttpResponse
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
    OpenWebUIChatCompletionRequestSerializer,
    OpenWebUIModelListSerializer,
)
from retrieval.services import answer_query


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
        serializer.is_valid(raise_exception=True)
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
