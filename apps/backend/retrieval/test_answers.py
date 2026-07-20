import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from retrieval.answers import (
    ANSWER_RESPONSE_FORMAT,
    SYSTEM_PROMPT,
    AnswerResponseError,
    ExtractiveAnswerGenerator,
    OpenRouterAnswerGenerator,
    build_answer_generator,
)
from retrieval.context import AssembledContext
from retrieval.types import RetrievedChunk, RetrievedFact


def answer_response(payload):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload)),
            )
        ]
    )


class ExtractiveAnswerGeneratorTests(SimpleTestCase):
    def test_chunk_answer_is_compact_and_bounded(self):
        context = AssembledContext(
            text="safe context",
            chunks=(RetrievedChunk(1, "1:0", "Accessible   answer. " + "x" * 900),),
        )

        result = ExtractiveAnswerGenerator().generate("question", context)

        self.assertTrue(result.supported)
        self.assertLessEqual(len(result.answer), 800)
        self.assertNotIn("  ", result.answer)

    def test_fact_only_context_returns_a_statement(self):
        fact = RetrievedFact(1, "1:0", "Sarah", "responsible_for", "Atlas", "Evidence")

        result = ExtractiveAnswerGenerator().generate(
            "question",
            AssembledContext(text="safe context", facts=(fact,)),
        )

        self.assertEqual(result.answer, "Sarah responsible for Atlas.")
        self.assertTrue(result.supported)

    def test_empty_context_is_unsupported(self):
        result = ExtractiveAnswerGenerator().generate("question", AssembledContext())

        self.assertFalse(result.supported)
        self.assertEqual(result.answer, "")


class OpenRouterAnswerGeneratorTests(SimpleTestCase):
    def test_request_contains_only_supplied_context_and_uses_strict_json(self):
        client = MagicMock()
        client.chat.completions.create.return_value = answer_response(
            {"answer": "Sarah owns Atlas.", "supported": True}
        )
        generator = OpenRouterAnswerGenerator(client=client, model="answer-model", max_tokens=300)
        context = AssembledContext(text='{"source":"S1","content":"Allowed only"}')

        result = generator.generate("Who owns Atlas?", context)

        self.assertEqual(result.answer, "Sarah owns Atlas.")
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "answer-model")
        self.assertEqual(request["temperature"], 0.0)
        self.assertEqual(request["max_tokens"], 300)
        self.assertEqual(request["response_format"], ANSWER_RESPONSE_FORMAT)
        self.assertEqual(request["messages"][0], {"role": "system", "content": SYSTEM_PROMPT})
        self.assertIn("Allowed only", request["messages"][1]["content"])
        self.assertNotIn("Restricted", request["messages"][1]["content"])
        client.chat.completions.create.assert_called_once()

    def test_empty_context_never_calls_openrouter(self):
        client = MagicMock()
        generator = OpenRouterAnswerGenerator(client=client, model="answer-model", max_tokens=300)

        result = generator.generate("question", AssembledContext())

        self.assertFalse(result.supported)
        client.chat.completions.create.assert_not_called()

    def test_provider_can_request_a_controlled_refusal(self):
        client = MagicMock()
        client.chat.completions.create.return_value = answer_response(
            {"answer": "Not enough evidence.", "supported": False}
        )
        generator = OpenRouterAnswerGenerator(client=client, model="answer-model", max_tokens=300)

        result = generator.generate("question", AssembledContext(text="safe context"))

        self.assertFalse(result.supported)

    def test_malformed_provider_responses_are_rejected(self):
        malformed = (
            SimpleNamespace(choices=[]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))]),
            answer_response({"answer": "missing supported"}),
            answer_response({"answer": "", "supported": True}),
            answer_response({"answer": "answer", "supported": "yes"}),
        )
        for response in malformed:
            with self.subTest(response=response):
                client = MagicMock()
                client.chat.completions.create.return_value = response
                generator = OpenRouterAnswerGenerator(
                    client=client,
                    model="answer-model",
                    max_tokens=300,
                )
                with self.assertRaises(AnswerResponseError):
                    generator.generate("question", AssembledContext(text="safe context"))
                self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_contract_failure_retries_once_with_the_same_safe_request(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            SimpleNamespace(choices=[]),
            answer_response({"answer": "Recovered answer.", "supported": True}),
        ]
        generator = OpenRouterAnswerGenerator(client=client, model="answer-model", max_tokens=300)
        context = AssembledContext(text='{"source":"S1","content":"Allowed only"}')

        with self.assertLogs("retrieval.answers", level="WARNING") as logs:
            result = generator.generate("question", context)

        self.assertEqual(result.answer, "Recovered answer.")
        self.assertEqual(client.chat.completions.create.call_count, 2)
        first_request, second_request = client.chat.completions.create.call_args_list
        self.assertEqual(first_request.kwargs, second_request.kwargs)
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("Allowed only", logs.output[0])

    def test_non_contract_provider_failure_is_not_retried(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = TimeoutError("provider timeout payload")
        generator = OpenRouterAnswerGenerator(client=client, model="answer-model", max_tokens=300)

        with self.assertRaises(TimeoutError):
            generator.generate("question", AssembledContext(text="safe context"))

        client.chat.completions.create.assert_called_once()

    @override_settings(QUERY_ANSWER_PROVIDER="extractive")
    def test_builder_returns_extractive_generator_when_remote_answers_are_disabled(self):
        self.assertIsInstance(build_answer_generator(), ExtractiveAnswerGenerator)

    @override_settings(
        QUERY_ANSWER_PROVIDER="openrouter",
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_BASE_URL="https://openrouter.ai/api/v1",
        OPENROUTER_SITE_URL="https://knowledge.example.com",
        OPENROUTER_APP_NAME="Knowledge Graph",
        OPENROUTER_REQUEST_TIMEOUT_SECONDS=15.0,
        OPENROUTER_MODEL="answer-model",
        QUERY_ANSWER_MAX_TOKENS=600,
    )
    @patch("retrieval.answers.OpenAI")
    def test_builder_configures_openrouter_without_making_a_request(self, mock_openai):
        generator = build_answer_generator()

        self.assertIsInstance(generator, OpenRouterAnswerGenerator)
        mock_openai.assert_called_once_with(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            timeout=15.0,
            max_retries=0,
            default_headers={
                "X-OpenRouter-Title": "Knowledge Graph",
                "HTTP-Referer": "https://knowledge.example.com",
            },
        )
        mock_openai.return_value.chat.completions.create.assert_not_called()
