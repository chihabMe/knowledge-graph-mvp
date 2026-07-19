from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from retrieval.identity import TrustedIdentityUnavailable, trusted_user_email
from retrieval.serializers import QueryRequestSerializer
from retrieval.services import answer_query


class QueryView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "query"

    def post(self, request):
        serializer = QueryRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user_email = trusted_user_email(request.user)
        except TrustedIdentityUnavailable:
            return Response(
                {"detail": "Authenticated identity is unavailable."},
                status=status.HTTP_403_FORBIDDEN,
            )

        result = answer_query(serializer.validated_data["question"], user_email)
        return Response(result.as_payload())
