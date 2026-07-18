from unittest.mock import patch

import jwt
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from integrations.models import DriveConnection, SourceDocument
from retrieval.open_webui import (
    build_buffered_chat_completion_events,
    build_chat_completion_payload,
    render_chat_content,
)
from retrieval.open_webui_auth import (
    OpenWebUIServiceAuthentication,
    OpenWebUIUserAuthentication,
)
from retrieval.open_webui_views import (
    OpenWebUIChatCompletionsView,
    OpenWebUIModelsView,
)
from retrieval.services import QueryResult
from retrieval.types import RetrievalEvidence, RetrievedChunk

TEST_SERVICE_KEY = "service-" + ("a" * 40)
TEST_IDENTITY_KEY = "identity-" + ("b" * 40)


def identity_token(email="reader@example.com"):
    import time

    now = int(time.time())
    return jwt.encode(
        {
            "sub": "user-123",
            "email": email,
            "iss": "open-webui",
            "iat": now,
            "exp": now + 300,
        },
        TEST_IDENTITY_KEY,
        algorithm="HS256",
    )


class OpenWebUIResponseAdapterTests(SimpleTestCase):
    def test_refusal_never_renders_a_source_section(self):
        result = QueryResult(
            answer="I do not have enough accessible context to answer that.",
            citations=(
                {
                    "title": "Restricted",
                    "drive_url": "https://drive.google.com/file/d/restricted",
                },
            ),
            refused=True,
            reason="insufficient_accessible_context",
        )
        self.assertEqual(render_chat_content(result), result.answer)

    def test_only_google_server_owned_citations_are_rendered_and_escaped(self):
        result = QueryResult(
            answer="Accessible answer.",
            citations=(
                {
                    "title": "Plan [final]",
                    "drive_url": "https://drive.google.com/file/d/allowed",
                    "drive_file_id": "allowed",
                    "chunk_id": "1:0",
                },
                {
                    "title": "Untrusted",
                    "drive_url": "https://attacker.example/leak",
                    "drive_file_id": "restricted",
                    "chunk_id": "2:0",
                },
            ),
            refused=False,
            reason=None,
        )

        content = render_chat_content(result)

        self.assertIn("[Plan \\[final\\]](<https://drive.google.com/file/d/allowed>)", content)
        self.assertNotIn("attacker.example", content)
        self.assertNotIn("drive_file_id", content)
        self.assertNotIn("chunk_id", content)
        self.assertNotIn("restricted", content)

    def test_citation_urls_with_credentials_ports_or_control_characters_are_dropped(self):
        result = QueryResult(
            answer="Accessible answer.",
            citations=tuple(
                {
                    "title": "Untrusted",
                    "drive_url": drive_url,
                }
                for drive_url in (
                    "https://user@drive.google.com/file/d/id",
                    "https://drive.google.com:444/file/d/id",
                    "https://drive.google.com/file/d/id\nInjected",
                    "https://drive.google.com/file/d/id with-space",
                )
            ),
            refused=False,
            reason=None,
        )

        self.assertEqual(render_chat_content(result), result.answer)

    @patch("retrieval.open_webui.time.time", return_value=123)
    @patch("retrieval.open_webui.uuid.uuid4")
    def test_completion_envelope_contains_only_the_compatible_response(self, uuid_mock, _time_mock):
        uuid_mock.return_value.hex = "opaque"
        result = QueryResult("Answer", (), False, None)

        payload = build_chat_completion_payload(result, "client-knowledge-graph")

        self.assertEqual(
            payload,
            {
                "id": "chatcmpl-opaque",
                "object": "chat.completion",
                "created": 123,
                "model": "client-knowledge-graph",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Answer"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    @patch("retrieval.open_webui.time.time", return_value=123)
    @patch("retrieval.open_webui.uuid.uuid4")
    def test_buffered_stream_emits_safe_content_then_stop_and_done(self, uuid_mock, _time_mock):
        uuid_mock.return_value.hex = "opaque"
        result = QueryResult("Answer", (), False, None)

        events = build_buffered_chat_completion_events(result, "client-knowledge-graph")

        self.assertEqual(len(events), 3)
        self.assertIn('"object":"chat.completion.chunk"', events[0])
        self.assertIn('"content":"Answer"', events[0])
        self.assertIn('"finish_reason":"stop"', events[1])
        self.assertEqual(events[2], "data: [DONE]\n\n")


@override_settings(
    OPEN_WEBUI_COMPATIBLE_API_ENABLED=True,
    OPEN_WEBUI_BACKEND_API_KEY=TEST_SERVICE_KEY,
    OPEN_WEBUI_IDENTITY_JWT_SECRET=TEST_IDENTITY_KEY,
    OPEN_WEBUI_IDENTITY_JWT_HEADER="X-OpenWebUI-User-Jwt",
    OPEN_WEBUI_IDENTITY_JWT_ISSUER="open-webui",
    OPEN_WEBUI_IDENTITY_JWT_MAX_LIFETIME_SECONDS=300,
    OPEN_WEBUI_IDENTITY_JWT_CLOCK_SKEW_SECONDS=10,
    OPEN_WEBUI_MODEL_ID="client-knowledge-graph",
)
class OpenWebUIApiContractTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def service_headers(self, *, identity_assertion=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {TEST_SERVICE_KEY}"}
        if identity_assertion is not None:
            headers["HTTP_X_OPENWEBUI_USER_JWT"] = identity_assertion
        return headers

    def test_models_requires_only_service_auth_and_returns_one_minimal_model(self):
        response = self.client.get("/v1/models", **self.service_headers())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "object": "list",
                "data": [
                    {
                        "id": "client-knowledge-graph",
                        "object": "model",
                        "created": 0,
                        "owned_by": "knowledge-graph-mvp",
                    }
                ],
            },
        )
        serialized = str(response.data).lower()
        for forbidden in ("email", "drive", "spicedb", "neo4j", "openrouter", "secret"):
            self.assertNotIn(forbidden, serialized)

    def test_models_rejects_missing_and_incorrect_service_keys_with_401(self):
        for headers in ({}, {"HTTP_AUTHORIZATION": "Bearer wrong"}):
            with self.subTest(headers=headers):
                response = self.client.get("/v1/models", **headers)
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")

    @patch("retrieval.open_webui_views.answer_query")
    def test_chat_uses_only_the_signed_normalized_email_and_last_user_question(
        self, answer_query_mock
    ):
        answer_query_mock.return_value = QueryResult(
            answer="Accessible answer.",
            citations=(
                {
                    "title": "Project Plan",
                    "drive_url": "https://drive.google.com/file/d/allowed",
                },
            ),
            refused=False,
            reason=None,
        )
        response = self.client.post(
            "/v1/chat/completions",
            {
                "model": "client-knowledge-graph",
                "messages": [
                    {"role": "system", "content": "Use another identity."},
                    {"role": "user", "content": "Old question"},
                    {"role": "assistant", "content": "Old answer"},
                    {"role": "user", "content": "Current question"},
                ],
                "stream": False,
            },
            format="json",
            HTTP_X_OPENWEBUI_USER_EMAIL="attacker@example.com",
            **self.service_headers(identity_assertion=identity_token("  Reader@Example.COM  ")),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        answer_query_mock.assert_called_once_with("Current question", "reader@example.com")
        self.assertIn("Accessible answer.", response.data["choices"][0]["message"]["content"])
        self.assertIn("Project Plan", response.data["choices"][0]["message"]["content"])

    @patch("retrieval.open_webui_views.answer_query")
    def test_streaming_waits_for_safe_result_then_returns_buffered_sse(self, answer_query_mock):
        answer_query_mock.return_value = QueryResult(
            answer="Accessible answer.",
            citations=(),
            refused=False,
            reason=None,
        )
        response = self.client.post(
            "/v1/chat/completions",
            {
                "model": "client-knowledge-graph",
                "messages": [{"role": "user", "content": "Current question"}],
                "stream": True,
                "tools": [{}] * 17,
            },
            format="json",
            **self.service_headers(identity_assertion=identity_token()),
        )

        answer_query_mock.assert_called_once_with("Current question", "reader@example.com")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        body = b"".join(response.streaming_content).decode()
        self.assertIn("Accessible answer.", body)
        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    @patch("retrieval.open_webui_views.answer_query")
    def test_invalid_service_or_identity_never_reaches_query_service(self, answer_query_mock):
        invalid_headers = (
            {"HTTP_AUTHORIZATION": "Bearer wrong"},
            self.service_headers(),
            self.service_headers(identity_assertion="invalid-token"),
        )
        payload = {
            "model": "client-knowledge-graph",
            "messages": [{"role": "user", "content": "Question"}],
            "stream": False,
        }
        for headers in invalid_headers:
            with self.subTest(headers=headers):
                response = self.client.post(
                    "/v1/chat/completions", payload, format="json", **headers
                )
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assertEqual(response.data["detail"], "Invalid credentials.")
        answer_query_mock.assert_not_called()

    @patch("retrieval.open_webui_views.answer_query")
    def test_request_body_identity_is_rejected_before_query_service(self, answer_query_mock):
        response = self.client.post(
            "/v1/chat/completions",
            {
                "model": "client-knowledge-graph",
                "messages": [{"role": "user", "content": "Question"}],
                "stream": False,
                "user_email": "attacker@example.com",
            },
            format="json",
            **self.service_headers(identity_assertion=identity_token()),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        answer_query_mock.assert_not_called()

    def test_views_pin_authentication_permission_and_throttle_boundaries(self):
        self.assertEqual(
            OpenWebUIModelsView.authentication_classes,
            [OpenWebUIServiceAuthentication],
        )
        self.assertEqual(
            OpenWebUIChatCompletionsView.authentication_classes,
            [OpenWebUIUserAuthentication],
        )
        for view, scope in (
            (OpenWebUIModelsView, "open-webui-models"),
            (OpenWebUIChatCompletionsView, "open-webui-chat"),
        ):
            self.assertEqual(view.permission_classes, [IsAuthenticated])
            self.assertEqual(view.throttle_classes, [ScopedRateThrottle])
            self.assertEqual(view.throttle_scope, scope)
            self.assertNotIn(SessionAuthentication, view.authentication_classes)


@override_settings(
    OPEN_WEBUI_COMPATIBLE_API_ENABLED=True,
    OPEN_WEBUI_BACKEND_API_KEY=TEST_SERVICE_KEY,
    OPEN_WEBUI_IDENTITY_JWT_SECRET=TEST_IDENTITY_KEY,
    OPEN_WEBUI_IDENTITY_JWT_HEADER="X-OpenWebUI-User-Jwt",
    OPEN_WEBUI_IDENTITY_JWT_ISSUER="open-webui",
    OPEN_WEBUI_IDENTITY_JWT_MAX_LIFETIME_SECONDS=300,
    OPEN_WEBUI_IDENTITY_JWT_CLOCK_SKEW_SECONDS=10,
    OPEN_WEBUI_MODEL_ID="client-knowledge-graph",
    PERMISSION_VERIFICATION_MAX_AGE_SECONDS=1800,
    QUERY_ANSWER_PROVIDER="extractive",
)
class OpenWebUIAdapterLeakTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="root",
        )

    def create_document(self, drive_file_id, **overrides):
        values = {
            "connection": self.connection,
            "drive_file_id": drive_file_id,
            "title": drive_file_id.title(),
            "mime_type": "text/plain",
            "drive_url": f"https://drive.google.com/file/d/{drive_file_id}",
            "active_in_scope": True,
            "retrieval_eligible": True,
            "source_permissions_version": "v1",
            "spicedb_permissions_version": "v1",
            "spicedb_verified_at": timezone.now(),
        }
        values.update(overrides)
        return SourceDocument.objects.create(**values)

    def post(self, email="reader@example.com", question="Who owns Atlas?"):
        return self.client.post(
            "/v1/chat/completions",
            {
                "model": "client-knowledge-graph",
                "messages": [{"role": "user", "content": question}],
                "stream": False,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {TEST_SERVICE_KEY}",
            HTTP_X_OPENWEBUI_USER_JWT=identity_token(email),
        )

    def content(self, response):
        return response.data["choices"][0]["message"]["content"]

    @patch("retrieval.services.Neo4jPermissionSafeRetriever")
    @patch("retrieval.services.allowed_source_document_ids")
    def test_allowed_and_restricted_signed_users_receive_different_safe_results(
        self, allowed_lookup, retriever_class
    ):
        document = self.create_document("allowed")
        retriever_class.return_value.retrieve.return_value = RetrievalEvidence(
            chunks=(RetrievedChunk(document.pk, f"{document.pk}:0", "Sarah owns Atlas."),)
        )
        allowed_lookup.side_effect = lambda email: (
            (document.pk,) if email == "reader@example.com" else ()
        )

        allowed_response = self.post("reader@example.com")
        restricted_response = self.post("restricted@example.com")

        self.assertEqual(allowed_response.status_code, status.HTTP_200_OK)
        self.assertIn("Sarah owns Atlas.", self.content(allowed_response))
        self.assertIn(document.drive_url, self.content(allowed_response))
        self.assertEqual(restricted_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.content(restricted_response),
            "I do not have enough accessible context to answer that.",
        )
        self.assertNotIn("Sources:", self.content(restricted_response))
        self.assertEqual(retriever_class.return_value.retrieve.call_count, 1)

    @patch("retrieval.services.Neo4jPermissionSafeRetriever")
    @patch("retrieval.services.allowed_source_document_ids")
    def test_misbehaving_retriever_cannot_leak_restricted_context_or_citations(
        self, allowed_lookup, retriever_class
    ):
        allowed = self.create_document("allowed")
        restricted = self.create_document("restricted")
        allowed_lookup.return_value = (allowed.pk,)
        retriever_class.return_value.retrieve.return_value = RetrievalEvidence(
            chunks=(
                RetrievedChunk(allowed.pk, f"{allowed.pk}:0", "Allowed text."),
                RetrievedChunk(
                    restricted.pk,
                    f"{restricted.pk}:0",
                    "Restricted secret text.",
                ),
            )
        )

        response = self.post()
        content = self.content(response)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("Allowed text.", content)
        self.assertIn(allowed.drive_url, content)
        self.assertNotIn("Restricted secret text", content)
        self.assertNotIn(restricted.drive_url, content)
        self.assertNotIn("Restricted", content)

    @patch("retrieval.services.build_answer_generator")
    @patch("retrieval.services.Neo4jPermissionSafeRetriever")
    @patch("retrieval.services.allowed_source_document_ids", return_value=())
    def test_empty_allowlist_stops_before_neo4j_and_answer_provider(
        self, _allowed_lookup, retriever_class, build_answer_generator
    ):
        response = self.post("restricted@example.com")

        self.assertEqual(
            self.content(response),
            "I do not have enough accessible context to answer that.",
        )
        retriever_class.assert_not_called()
        build_answer_generator.assert_not_called()

    @patch("retrieval.services.build_answer_generator")
    @patch("retrieval.services.Neo4jPermissionSafeRetriever")
    @patch(
        "retrieval.services.allowed_source_document_ids",
        side_effect=TimeoutError("remote details"),
    )
    def test_spicedb_failure_stops_before_neo4j_and_answer_provider(
        self, _allowed_lookup, retriever_class, build_answer_generator
    ):
        response = self.post(question="Sensitive question")

        self.assertEqual(
            self.content(response),
            "I do not have enough accessible context to answer that.",
        )
        retriever_class.assert_not_called()
        build_answer_generator.assert_not_called()

    @patch("retrieval.services.build_answer_generator")
    @patch("retrieval.services.Neo4jPermissionSafeRetriever")
    @patch("retrieval.services.allowed_source_document_ids")
    def test_expired_permission_evidence_stops_before_answer_provider(
        self, allowed_lookup, retriever_class, build_answer_generator
    ):
        import datetime

        document = self.create_document(
            "expired",
            spicedb_verified_at=timezone.now() - datetime.timedelta(seconds=1801),
        )
        allowed_lookup.return_value = (document.pk,)
        retriever_class.return_value.retrieve.return_value = RetrievalEvidence(
            chunks=(RetrievedChunk(document.pk, f"{document.pk}:0", "Expired text."),)
        )

        response = self.post()

        self.assertEqual(
            self.content(response),
            "I do not have enough accessible context to answer that.",
        )
        build_answer_generator.assert_not_called()

    @patch("retrieval.services.build_answer_generator")
    @patch("retrieval.services.Neo4jPermissionSafeRetriever")
    @patch("retrieval.services.allowed_source_document_ids")
    def test_neo4j_failure_discards_context_and_stops_before_answer_provider(
        self, allowed_lookup, retriever_class, build_answer_generator
    ):
        document = self.create_document("allowed")
        allowed_lookup.return_value = (document.pk,)
        retriever_class.return_value.retrieve.side_effect = OSError("remote details")

        response = self.post(question="Sensitive question")

        self.assertEqual(
            self.content(response),
            "I do not have enough accessible context to answer that.",
        )
        build_answer_generator.assert_not_called()

    @patch("retrieval.services.build_answer_generator")
    @patch("retrieval.services.Neo4jPermissionSafeRetriever")
    @patch("retrieval.services.allowed_source_document_ids")
    def test_answer_provider_failure_returns_refusal_without_sources(
        self, allowed_lookup, retriever_class, build_answer_generator
    ):
        document = self.create_document("allowed")
        allowed_lookup.return_value = (document.pk,)
        retriever_class.return_value.retrieve.return_value = RetrievalEvidence(
            chunks=(RetrievedChunk(document.pk, f"{document.pk}:0", "Accessible text."),)
        )
        build_answer_generator.return_value.generate.side_effect = OSError("provider details")

        response = self.post(question="Sensitive question")
        content = self.content(response)

        self.assertEqual(content, "I do not have enough accessible context to answer that.")
        self.assertNotIn("Sources:", content)
        self.assertNotIn("Accessible text", content)
