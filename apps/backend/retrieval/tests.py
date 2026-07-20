import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from graph.guard import ALLOWED_DOCUMENTS_PARAMETER, PROVENANCE_FIELDS
from integrations.models import DriveConnection, SourceDocument
from retrieval.answers import GeneratedAnswer
from retrieval.context import AssembledContext
from retrieval.neo4j import (
    CHUNK_RETRIEVAL_CYPHER,
    FACT_RETRIEVAL_CYPHER,
    VECTOR_RETRIEVAL_CYPHER,
    Neo4jPermissionSafeRetriever,
    fuse_chunk_rankings,
    question_terms,
    vector_retrieval_cypher,
)
from retrieval.services import QueryResult, answer_query
from retrieval.types import RetrievalEvidence, RetrievedChunk, RetrievedFact
from retrieval.views import QueryView


def provenance(source_document_id=1, **overrides):
    values = {
        "source_document_id": source_document_id,
        "connection_id": 10,
        "drive_file_id": f"drive-{source_document_id}",
        "source_permissions_version": "v1",
    }
    values.update(overrides)
    return values


def chunk_record(source_document_id=1, **chunk_overrides):
    chunk = {
        **provenance(source_document_id),
        "chunk_id": f"{source_document_id}:0",
        "chunk_index": 0,
        "text": "Sarah owns the Atlas project.",
        **chunk_overrides,
    }
    return {
        "chunk": chunk,
        "belongs": provenance(source_document_id),
        "document": {**provenance(source_document_id), "content_hash": "content-v1"},
        "relevance": 2,
    }


def fact_record(source_document_id=1):
    return {
        "source": {**provenance(source_document_id), "name": "Sarah"},
        "fact": {**provenance(source_document_id), "chunk_index": 0},
        "target": {**provenance(source_document_id), "name": "Atlas"},
        "chunk": {
            **provenance(source_document_id),
            "chunk_id": f"{source_document_id}:0",
            "chunk_index": 0,
            "text": "Sarah owns the Atlas project.",
        },
        "belongs": provenance(source_document_id),
        "document": {**provenance(source_document_id), "content_hash": "content-v1"},
        "relationship_type": "responsible_for",
        "relevance": 2,
    }


class StubEmbeddingAdapter:
    def __init__(self, query_embedding=(), error=None):
        self.query_embedding = query_embedding
        self.error = error
        self.questions = []

    def embed_chunks(self, chunks):
        raise AssertionError("Retrieval must not embed stored chunks.")

    def embed_query(self, question):
        self.questions.append(question)
        if self.error:
            raise self.error
        return self.query_embedding


class Neo4jRetrievalSecurityTests(SimpleTestCase):
    def test_every_cypher_path_guards_every_node_and_relationship_alias(self):
        aliases_by_query = {
            CHUNK_RETRIEVAL_CYPHER: ("chunk", "belongs", "document"),
            FACT_RETRIEVAL_CYPHER: (
                "source",
                "fact",
                "target",
                "chunk",
                "belongs",
                "document",
            ),
            VECTOR_RETRIEVAL_CYPHER: ("chunk", "belongs", "document"),
        }

        for query, aliases in aliases_by_query.items():
            with self.subTest(query=query[:20]):
                if "matching_terms" in query:
                    self.assertIn(
                        "size(matching_terms) >= $minimum_should_match",
                        query,
                    )
                for alias in aliases:
                    for field in PROVENANCE_FIELDS:
                        self.assertIn(f"{alias}.{field} IS NOT NULL", query)
                    self.assertIn(
                        f"{alias}.source_document_id IN ${ALLOWED_DOCUMENTS_PARAMETER}",
                        query,
                    )

    def test_vector_similarity_is_computed_only_after_permission_and_provenance_where(self):
        query = vector_retrieval_cypher("cosine")

        self.assertLess(
            query.index("chunk.source_document_id IN $allowed_source_document_ids"),
            query.index("vector.similarity.cosine"),
        )
        self.assertNotIn("db.index.vector.queryNodes", query)

    def test_vector_query_rejects_unconfigured_similarity_function(self):
        with self.assertRaises(ValueError):
            vector_retrieval_cypher("dot")

    def test_question_terms_are_bounded_normalized_and_deduplicated(self):
        question = "Sarah SARAH owns Atlas 2026 " + " ".join(f"term{n}" for n in range(20))

        terms = question_terms(question)

        self.assertEqual(terms[:4], ("sarah", "owns", "atlas", "2026"))
        self.assertEqual(len(terms), 12)

    def test_question_terms_drop_generic_question_words(self):
        self.assertEqual(
            question_terms("Who owns the Atlas project?"),
            ("owns", "atlas", "project"),
        )

    @patch("retrieval.neo4j.session")
    def test_empty_allowlist_never_opens_a_neo4j_session(self, mock_session):
        embeddings = StubEmbeddingAdapter(query_embedding=(0.1, 0.2, 0.3))

        result = Neo4jPermissionSafeRetriever(embedding_adapter=embeddings).retrieve(
            "Who owns Atlas?", ()
        )

        self.assertEqual(result, RetrievalEvidence())
        self.assertEqual(embeddings.questions, [])
        mock_session.assert_not_called()

    @patch("retrieval.neo4j.session")
    def test_allowed_chunk_and_fact_are_returned_with_filtered_parameters(self, mock_session):
        db_session = MagicMock()
        db_session.run.side_effect = [[chunk_record()], [fact_record()]]
        mock_session.return_value.__enter__.return_value = db_session

        result = Neo4jPermissionSafeRetriever(limit=4).retrieve(
            "Who owns Atlas?", (1, 1, True, "2")
        )

        self.assertEqual(len(result.chunks), 1)
        self.assertEqual(len(result.facts), 1)
        self.assertEqual(result.chunks[0].content_version, "content-v1")
        self.assertEqual(result.facts[0].content_version, "content-v1")
        self.assertEqual(db_session.run.call_count, 2)
        for call in db_session.run.call_args_list:
            self.assertEqual(call.kwargs["allowed_source_document_ids"], [1])
            self.assertEqual(call.kwargs["query_terms"], ["owns", "atlas"])
            self.assertEqual(call.kwargs["minimum_should_match"], 2)
            self.assertEqual(call.kwargs["limit"], 4)

    @override_settings(GRAPH_CHUNK_EMBEDDING_DIMENSIONS=3)
    @patch("retrieval.neo4j.session")
    def test_vector_and_keyword_rankings_are_fused_with_filtered_parameters(self, mock_session):
        vector = chunk_record()
        vector["score"] = 0.91
        keyword = chunk_record()
        db_session = MagicMock()
        db_session.run.side_effect = [[vector], [keyword], [fact_record()]]
        mock_session.return_value.__enter__.return_value = db_session
        embeddings = StubEmbeddingAdapter(query_embedding=(0.1, 0.2, 0.3))

        result = Neo4jPermissionSafeRetriever(
            limit=4,
            embedding_adapter=embeddings,
            minimum_vector_score=0.5,
        ).retrieve("Who owns Atlas?", (1,))

        self.assertEqual(len(result.chunks), 1)
        self.assertEqual(result.chunks[0].retrieval_modes, ("keyword", "vector"))
        self.assertEqual(result.facts[0].retrieval_modes, ("graph",))
        self.assertEqual(embeddings.questions, ["Who owns Atlas?"])
        vector_call = db_session.run.call_args_list[0]
        self.assertIn("vector.similarity.cosine", vector_call.args[0])
        self.assertEqual(vector_call.kwargs["allowed_source_document_ids"], [1])
        self.assertEqual(vector_call.kwargs["query_embedding"], [0.1, 0.2, 0.3])
        self.assertEqual(vector_call.kwargs["embedding_dimensions"], 3)
        self.assertEqual(vector_call.kwargs["minimum_vector_score"], 0.5)

    @override_settings(GRAPH_CHUNK_EMBEDDING_DIMENSIONS=3)
    @patch("retrieval.neo4j.session")
    def test_vector_only_question_can_return_guarded_context(self, mock_session):
        vector = chunk_record()
        vector["score"] = 0.8
        db_session = MagicMock()
        db_session.run.return_value = [vector]
        mock_session.return_value.__enter__.return_value = db_session

        result = Neo4jPermissionSafeRetriever(
            embedding_adapter=StubEmbeddingAdapter(query_embedding=(0.1, 0.2, 0.3))
        ).retrieve("Who is there?", (1,))

        self.assertEqual(len(result.chunks), 1)
        self.assertEqual(db_session.run.call_count, 1)

    @override_settings(GRAPH_CHUNK_EMBEDDING_DIMENSIONS=3)
    @patch("retrieval.neo4j.session")
    def test_restricted_and_missing_provenance_vector_records_are_dropped(self, mock_session):
        restricted = chunk_record(2)
        restricted["score"] = 0.9
        missing = chunk_record(1)
        missing["score"] = 0.8
        missing["belongs"].pop("drive_file_id")
        db_session = MagicMock()
        db_session.run.side_effect = [[restricted, missing], [], []]
        mock_session.return_value.__enter__.return_value = db_session

        result = Neo4jPermissionSafeRetriever(
            embedding_adapter=StubEmbeddingAdapter(query_embedding=(0.1, 0.2, 0.3))
        ).retrieve("Who owns Atlas?", (1,))

        self.assertEqual(result, RetrievalEvidence())

    @patch("retrieval.neo4j.session")
    def test_missing_or_non_string_document_content_hash_becomes_unknown_version(
        self, mock_session
    ):
        absent = chunk_record(1)
        absent["document"].pop("content_hash")
        numeric = chunk_record(2)
        numeric["document"]["content_hash"] = 7
        db_session = MagicMock()
        db_session.run.side_effect = [[absent, numeric], []]
        mock_session.return_value.__enter__.return_value = db_session

        result = Neo4jPermissionSafeRetriever().retrieve("Who owns Atlas?", (1, 2))

        self.assertEqual([chunk.content_version for chunk in result.chunks], ["", ""])

    @override_settings(GRAPH_CHUNK_EMBEDDING_DIMENSIONS=3)
    @patch("retrieval.neo4j.session")
    def test_embedding_failure_or_wrong_dimensions_never_opens_neo4j(self, mock_session):
        for adapter in (
            StubEmbeddingAdapter(error=TimeoutError()),
            StubEmbeddingAdapter(query_embedding=(0.1, 0.2)),
        ):
            with self.subTest(adapter=adapter), self.assertRaises((TimeoutError, ValueError)):
                Neo4jPermissionSafeRetriever(embedding_adapter=adapter).retrieve(
                    "Who owns Atlas?", (1,)
                )
        mock_session.assert_not_called()

    def test_rank_fusion_rewards_chunks_found_by_both_paths(self):
        vector_only = RetrievedChunk(1, "1:0", "Vector only")
        both_vector = RetrievedChunk(1, "1:1", "Both")
        both_keyword = RetrievedChunk(1, "1:1", "Both")
        keyword_only = RetrievedChunk(1, "1:2", "Keyword only")

        result = fuse_chunk_rankings(
            (
                ("vector", (vector_only, both_vector)),
                ("keyword", (both_keyword, keyword_only)),
            ),
            limit=3,
        )

        self.assertEqual([chunk.chunk_id for chunk in result], ["1:1", "1:0", "1:2"])
        self.assertEqual(result[0].retrieval_modes, ("keyword", "vector"))

    @patch("retrieval.neo4j.session")
    def test_stopword_only_question_never_opens_a_neo4j_session(self, mock_session):
        result = Neo4jPermissionSafeRetriever().retrieve("Who is there?", (1,))

        self.assertEqual(result, RetrievalEvidence())
        mock_session.assert_not_called()

    @patch("retrieval.neo4j.session")
    def test_restricted_fact_connected_to_visible_nodes_is_dropped(self, mock_session):
        record = fact_record()
        record["fact"] = {**provenance(2), "chunk_index": 0}
        db_session = MagicMock()
        db_session.run.side_effect = [[], [record]]
        mock_session.return_value.__enter__.return_value = db_session

        result = Neo4jPermissionSafeRetriever().retrieve("Who owns Atlas?", (1,))

        self.assertEqual(result, RetrievalEvidence())

    @patch("retrieval.neo4j.session")
    def test_missing_provenance_nodes_and_relationships_are_dropped(self, mock_session):
        missing_node = fact_record()
        missing_node["target"] = dict(missing_node["target"])
        missing_node["target"].pop("source_permissions_version")
        missing_relationship = fact_record()
        missing_relationship["fact"] = dict(missing_relationship["fact"])
        missing_relationship["fact"].pop("drive_file_id")
        missing_structural_relationship = chunk_record()
        missing_structural_relationship["belongs"] = dict(
            missing_structural_relationship["belongs"]
        )
        missing_structural_relationship["belongs"].pop("connection_id")
        db_session = MagicMock()
        db_session.run.side_effect = [
            [missing_structural_relationship],
            [missing_node, missing_relationship],
        ]
        mock_session.return_value.__enter__.return_value = db_session

        result = Neo4jPermissionSafeRetriever().retrieve("Who owns Atlas?", (1,))

        self.assertEqual(result, RetrievalEvidence())


class StubRetriever:
    def __init__(self, evidence=None, error=None):
        self.evidence = evidence or RetrievalEvidence()
        self.error = error
        self.calls = []

    def retrieve(self, question, allowed_source_document_ids):
        self.calls.append((question, allowed_source_document_ids))
        if self.error:
            raise self.error
        return self.evidence


class StubAnswerGenerator:
    def __init__(self, answer="Generated answer.", supported=True, error=None):
        self.answer = answer
        self.supported = supported
        self.error = error
        self.calls: list[tuple[str, AssembledContext]] = []

    def generate(self, question, context):
        self.calls.append((question, context))
        if self.error:
            raise self.error
        return GeneratedAnswer(answer=self.answer, supported=self.supported)


@override_settings(PERMISSION_VERIFICATION_MAX_AGE_SECONDS=1800)
class QueryServiceSecurityTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com", root_folder_id="root"
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
            "content_hash": "content-v1",
        }
        values.update(overrides)
        return SourceDocument.objects.create(**values)

    def test_allowed_context_returns_extract_and_permitted_citation(self):
        document = self.create_document("allowed")
        evidence = RetrievalEvidence(
            chunks=(
                RetrievedChunk(
                    source_document_id=document.pk,
                    chunk_id=f"{document.pk}:0",
                    text="Sarah owns Atlas.",
                    content_version="content-v1",
                ),
            )
        )

        result = answer_query(
            "Who owns Atlas?",
            "reader@example.com",
            allowed_lookup=lambda _email: (document.pk,),
            retriever=StubRetriever(evidence),
        )

        self.assertFalse(result.refused)
        self.assertEqual(result.answer, "Sarah owns Atlas.")
        self.assertEqual(result.reason, None)
        self.assertEqual(
            result.citations,
            (
                {
                    "title": document.title,
                    "drive_file_id": document.drive_file_id,
                    "drive_url": document.drive_url,
                    "chunk_id": f"{document.pk}:0",
                },
            ),
        )

    def test_empty_allowlist_refuses_without_retrieval(self):
        retriever = StubRetriever()

        result = answer_query(
            "Who owns Atlas?",
            "restricted@example.com",
            allowed_lookup=lambda _email: (),
            retriever=retriever,
        )

        self.assertTrue(result.refused)
        self.assertEqual(result.citations, ())
        self.assertEqual(retriever.calls, [])

    @patch("retrieval.services.Neo4jPermissionSafeRetriever")
    def test_empty_allowlist_does_not_build_embedding_or_retrieval_clients(self, retriever_class):
        result = answer_query(
            "Who owns Atlas?",
            "restricted@example.com",
            allowed_lookup=lambda _email: (),
        )

        self.assertTrue(result.refused)
        retriever_class.assert_not_called()

    def test_permission_lookup_happens_before_neo4j_retrieval(self):
        document = self.create_document("allowed")
        call_order = []

        def lookup(_email):
            call_order.append("spicedb")
            return (document.pk,)

        class OrderedRetriever(StubRetriever):
            def retrieve(self, question, allowed_source_document_ids):
                call_order.append("neo4j")
                return super().retrieve(question, allowed_source_document_ids)

        answer_query(
            "Who owns Atlas?",
            "reader@example.com",
            allowed_lookup=lookup,
            retriever=OrderedRetriever(),
        )

        self.assertEqual(call_order, ["spicedb", "neo4j"])

    def test_no_relevant_graph_context_returns_controlled_refusal(self):
        document = self.create_document("allowed")

        result = answer_query(
            "Who owns Atlas?",
            "reader@example.com",
            allowed_lookup=lambda _email: (document.pk,),
            retriever=StubRetriever(),
        )

        self.assertTrue(result.refused)
        self.assertEqual(result.reason, "insufficient_accessible_context")
        self.assertEqual(result.citations, ())

    def test_spicedb_failure_refuses_without_retrieval(self):
        retriever = StubRetriever()

        def fail_lookup(_email):
            raise TimeoutError

        with self.assertLogs("retrieval.services", level="WARNING") as captured:
            result = answer_query(
                "Who owns Atlas?",
                "reader@example.com",
                allowed_lookup=fail_lookup,
                retriever=retriever,
            )

        self.assertTrue(result.refused)
        self.assertEqual(result.citations, ())
        self.assertEqual(retriever.calls, [])
        self.assertIn("TimeoutError", "\n".join(captured.output))
        self.assertNotIn("reader@example.com", "\n".join(captured.output))

    def test_retrieval_failure_discards_all_context(self):
        document = self.create_document("allowed")
        retriever = StubRetriever(error=OSError("remote payload"))

        with self.assertLogs("retrieval.services", level="WARNING") as captured:
            result = answer_query(
                "sensitive question",
                "reader@example.com",
                allowed_lookup=lambda _email: (document.pk,),
                retriever=retriever,
            )

        log_output = "\n".join(captured.output)
        self.assertTrue(result.refused)
        self.assertEqual(result.citations, ())
        self.assertIn("OSError", log_output)
        self.assertNotIn("sensitive question", log_output)
        self.assertNotIn("remote payload", log_output)

    def test_citations_intersect_spicedb_allowlist_even_if_retriever_misbehaves(self):
        allowed = self.create_document("allowed")
        restricted = self.create_document("restricted")
        evidence = RetrievalEvidence(
            chunks=(
                RetrievedChunk(
                    allowed.pk, f"{allowed.pk}:0", "Allowed text.", content_version="content-v1"
                ),
                RetrievedChunk(
                    restricted.pk,
                    f"{restricted.pk}:0",
                    "Restricted text.",
                    content_version="content-v1",
                ),
            )
        )

        generator = StubAnswerGenerator(answer="Allowed generated answer.")
        result = answer_query(
            "text",
            "reader@example.com",
            allowed_lookup=lambda _email: (allowed.pk,),
            retriever=StubRetriever(evidence),
            answer_generator=generator,
        )

        self.assertFalse(result.refused)
        self.assertEqual([citation["drive_file_id"] for citation in result.citations], ["allowed"])
        self.assertNotIn("Restricted", result.answer)
        prompt_context = generator.calls[0][1]
        self.assertEqual(
            [chunk.source_document_id for chunk in prompt_context.chunks],
            [allowed.pk],
        )
        self.assertNotIn("Restricted text", prompt_context.text)

    def test_inactive_ineligible_unverified_and_expired_documents_contribute_nothing(self):
        inactive = self.create_document("inactive", active_in_scope=False)
        ineligible = self.create_document("ineligible", retrieval_eligible=False)
        unverified = self.create_document("unverified", spicedb_permissions_version="old")
        expired = self.create_document(
            "expired",
            spicedb_verified_at=timezone.now() - datetime.timedelta(seconds=1801),
        )
        documents = (inactive, ineligible, unverified, expired)
        evidence = RetrievalEvidence(
            chunks=tuple(
                RetrievedChunk(
                    document.pk,
                    f"{document.pk}:0",
                    f"{document.title} text",
                    content_version="content-v1",
                )
                for document in documents
            )
        )

        result = answer_query(
            "text",
            "reader@example.com",
            allowed_lookup=lambda _email: tuple(document.pk for document in documents),
            retriever=StubRetriever(evidence),
        )

        self.assertTrue(result.refused)
        self.assertEqual(result.citations, ())

    def test_stale_content_version_is_excluded_from_context_and_citations(self):
        current = self.create_document("current")
        superseded = self.create_document("superseded", content_hash="content-v2")
        evidence = RetrievalEvidence(
            chunks=(
                RetrievedChunk(
                    current.pk, f"{current.pk}:0", "Current text.", content_version="content-v1"
                ),
                RetrievedChunk(
                    superseded.pk,
                    f"{superseded.pk}:0",
                    "Superseded text.",
                    content_version="content-v1",
                ),
            )
        )
        generator = StubAnswerGenerator(answer="Current generated answer.")

        result = answer_query(
            "text",
            "reader@example.com",
            allowed_lookup=lambda _email: (current.pk, superseded.pk),
            retriever=StubRetriever(evidence),
            answer_generator=generator,
        )

        self.assertFalse(result.refused)
        self.assertEqual([citation["drive_file_id"] for citation in result.citations], ["current"])
        prompt_context = generator.calls[0][1]
        self.assertNotIn("Superseded text", prompt_context.text)

    def test_all_stale_content_versions_return_the_controlled_refusal(self):
        document = self.create_document("allowed", content_hash="content-v2")
        evidence = RetrievalEvidence(
            chunks=(
                RetrievedChunk(
                    document.pk, f"{document.pk}:0", "Old text.", content_version="content-v1"
                ),
            ),
            facts=(
                RetrievedFact(
                    source_document_id=document.pk,
                    chunk_id=f"{document.pk}:0",
                    source_name="Sarah",
                    relationship_type="responsible_for",
                    target_name="Atlas",
                    text="Old text.",
                    content_version="content-v1",
                ),
            ),
        )

        result = answer_query(
            "Who owns Atlas?",
            "reader@example.com",
            allowed_lookup=lambda _email: (document.pk,),
            retriever=StubRetriever(evidence),
        )

        self.assertTrue(result.refused)
        self.assertEqual(result.reason, "insufficient_accessible_context")
        self.assertEqual(result.citations, ())

    def test_missing_or_empty_content_version_fails_closed(self):
        versioned = self.create_document("versioned")
        unversioned = self.create_document("unversioned", content_hash="")
        for chunk in (
            RetrievedChunk(versioned.pk, f"{versioned.pk}:0", "No graph version."),
            RetrievedChunk(
                unversioned.pk,
                f"{unversioned.pk}:0",
                "Empty on both sides.",
                content_version="",
            ),
        ):
            with self.subTest(chunk=chunk):
                result = answer_query(
                    "text",
                    "reader@example.com",
                    allowed_lookup=lambda _email, pk=chunk.source_document_id: (pk,),
                    retriever=StubRetriever(RetrievalEvidence(chunks=(chunk,))),
                )

                self.assertTrue(result.refused)
                self.assertEqual(result.citations, ())

    def test_fact_only_context_returns_cited_extract_without_llm(self):
        document = self.create_document("allowed")
        evidence = RetrievalEvidence(
            facts=(
                RetrievedFact(
                    source_document_id=document.pk,
                    chunk_id=f"{document.pk}:0",
                    source_name="Sarah",
                    relationship_type="responsible_for",
                    target_name="Atlas",
                    text="Sarah owns Atlas.",
                    content_version="content-v1",
                ),
            )
        )

        result = answer_query(
            "Who owns Atlas?",
            "reader@example.com",
            allowed_lookup=lambda _email: (document.pk,),
            retriever=StubRetriever(evidence),
        )

        self.assertEqual(result.answer, "Sarah responsible for Atlas.")
        self.assertFalse(result.refused)
        self.assertEqual(result.citations[0]["drive_file_id"], "allowed")

    def test_answer_provider_is_not_called_before_permission_and_context_gates(self):
        document = self.create_document("allowed")
        generator = StubAnswerGenerator()

        for allowed_lookup, retriever in (
            (lambda _email: (), StubRetriever()),
            (lambda _email: (document.pk,), StubRetriever()),
        ):
            with self.subTest(allowed_lookup=allowed_lookup):
                result = answer_query(
                    "question",
                    "reader@example.com",
                    allowed_lookup=allowed_lookup,
                    retriever=retriever,
                    answer_generator=generator,
                )
                self.assertTrue(result.refused)

        self.assertEqual(generator.calls, [])

    def test_answer_provider_failure_discards_context_and_logs_no_payload(self):
        document = self.create_document("allowed")
        evidence = RetrievalEvidence(
            chunks=(
                RetrievedChunk(
                    document.pk,
                    f"{document.pk}:0",
                    "Secret allowed text.",
                    content_version="content-v1",
                ),
            )
        )
        generator = StubAnswerGenerator(error=OSError("provider response body"))

        with self.assertLogs("retrieval.services", level="WARNING") as captured:
            result = answer_query(
                "sensitive question",
                "reader@example.com",
                allowed_lookup=lambda _email: (document.pk,),
                retriever=StubRetriever(evidence),
                answer_generator=generator,
            )

        log_output = "\n".join(captured.output)
        self.assertTrue(result.refused)
        self.assertEqual(result.citations, ())
        self.assertNotIn("Secret allowed text", log_output)
        self.assertNotIn("sensitive question", log_output)
        self.assertNotIn("provider response body", log_output)

    def test_unsupported_model_answer_returns_the_shared_refusal_without_citations(self):
        document = self.create_document("allowed")
        evidence = RetrievalEvidence(
            chunks=(
                RetrievedChunk(
                    document.pk,
                    f"{document.pk}:0",
                    "Accessible text.",
                    content_version="content-v1",
                ),
            )
        )

        result = answer_query(
            "question",
            "reader@example.com",
            allowed_lookup=lambda _email: (document.pk,),
            retriever=StubRetriever(evidence),
            answer_generator=StubAnswerGenerator(answer="No answer", supported=False),
        )

        self.assertTrue(result.refused)
        self.assertEqual(result.answer, "I do not have enough accessible context to answer that.")
        self.assertEqual(result.citations, ())

    @override_settings(QUERY_CONTEXT_MAX_CHARS=100)
    def test_citations_cover_only_evidence_that_fit_the_prompt_context(self):
        document = self.create_document("allowed")
        first = RetrievedChunk(
            document.pk,
            f"{document.pk}:0",
            "First accessible source.",
            content_version="content-v1",
        )
        second = RetrievedChunk(
            document.pk,
            f"{document.pk}:1",
            "Second accessible source.",
            content_version="content-v1",
        )
        generator = StubAnswerGenerator()

        result = answer_query(
            "question",
            "reader@example.com",
            allowed_lookup=lambda _email: (document.pk,),
            retriever=StubRetriever(RetrievalEvidence(chunks=(first, second))),
            answer_generator=generator,
        )

        self.assertFalse(result.refused)
        self.assertEqual([citation["chunk_id"] for citation in result.citations], [first.chunk_id])
        self.assertEqual(generator.calls[0][1].chunks, (first,))


class QueryApiSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="reader", email="Trusted.Reader@Example.com"
        )

    @patch("retrieval.views.answer_query")
    def test_unauthenticated_request_is_rejected_before_query_service(self, answer_query_mock):
        response = self.client.post("/api/query/", {"question": "Who owns Atlas?"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        answer_query_mock.assert_not_called()

    @patch("retrieval.views.answer_query")
    def test_request_rejects_spoofed_identity_field(self, answer_query_mock):
        self.client.force_login(self.user)

        response = self.client.post(
            "/api/query/",
            {"question": "Who owns Atlas?", "user_email": "attacker@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["user_email"], ["Unexpected field."])
        answer_query_mock.assert_not_called()

    @patch("retrieval.views.answer_query")
    def test_authenticated_server_email_is_normalized_and_used(self, answer_query_mock):
        answer_query_mock.return_value = QueryResult(
            answer="Accessible answer.", citations=(), refused=False, reason=None
        )
        self.client.force_login(self.user)

        response = self.client.post("/api/query/", {"question": "Who owns Atlas?"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        answer_query_mock.assert_called_once_with("Who owns Atlas?", "trusted.reader@example.com")
        self.assertEqual(set(response.data), {"answer", "citations", "refused", "reason"})

    @patch("retrieval.views.answer_query")
    def test_authenticated_user_without_email_fails_closed(self, answer_query_mock):
        self.user.email = ""
        self.user.save(update_fields=["email"])
        self.client.force_login(self.user)

        response = self.client.post("/api/query/", {"question": "Who owns Atlas?"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        answer_query_mock.assert_not_called()

    def test_query_view_pins_session_authentication_permission_and_throttle(self):
        from rest_framework.authentication import SessionAuthentication
        from rest_framework.permissions import IsAuthenticated
        from rest_framework.throttling import ScopedRateThrottle

        self.assertEqual(QueryView.authentication_classes, [SessionAuthentication])
        self.assertEqual(QueryView.permission_classes, [IsAuthenticated])
        self.assertEqual(QueryView.throttle_classes, [ScopedRateThrottle])
        self.assertEqual(QueryView.throttle_scope, "query")

    @override_settings(PERMISSION_VERIFICATION_MAX_AGE_SECONDS=1800)
    @patch("retrieval.services.Neo4jPermissionSafeRetriever")
    @patch("retrieval.services.allowed_source_document_ids")
    def test_allowed_and_restricted_users_receive_different_safe_results(
        self, allowed_lookup, retriever_class
    ):
        connection = DriveConnection.objects.create(
            workspace_domain="example.com", root_folder_id="root"
        )
        document = SourceDocument.objects.create(
            connection=connection,
            drive_file_id="allowed",
            title="Allowed",
            mime_type="text/plain",
            drive_url="https://drive.google.com/file/d/allowed",
            active_in_scope=True,
            retrieval_eligible=True,
            source_permissions_version="v1",
            spicedb_permissions_version="v1",
            spicedb_verified_at=timezone.now(),
            content_hash="content-v1",
        )
        retriever_class.return_value.retrieve.return_value = RetrievalEvidence(
            chunks=(
                RetrievedChunk(
                    document.pk, f"{document.pk}:0", "Allowed answer.", content_version="content-v1"
                ),
            )
        )
        allowed_lookup.side_effect = lambda email: (
            (document.pk,) if email == "trusted.reader@example.com" else ()
        )
        restricted_user = get_user_model().objects.create_user(
            username="restricted", email="restricted@example.com"
        )

        self.client.force_login(self.user)
        allowed_response = self.client.post("/api/query/", {"question": "Allowed?"}, format="json")
        self.client.logout()
        self.client.force_login(restricted_user)
        restricted_response = self.client.post(
            "/api/query/", {"question": "Allowed?"}, format="json"
        )

        self.assertEqual(allowed_response.status_code, status.HTTP_200_OK)
        self.assertFalse(allowed_response.data["refused"])
        self.assertEqual(allowed_response.data["citations"][0]["drive_file_id"], "allowed")
        self.assertEqual(restricted_response.status_code, status.HTTP_200_OK)
        self.assertTrue(restricted_response.data["refused"])
        self.assertEqual(restricted_response.data["citations"], [])
        self.assertEqual(retriever_class.return_value.retrieve.call_count, 1)
