from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

import graph.db as graph_db
from graph.embeddings import (
    ChunkEmbedding,
    ChunkEmbeddingValidationError,
    EmbeddingResponseError,
    NoOpEmbeddingAdapter,
    OpenRouterEmbeddingAdapter,
    build_embedding_adapter,
    validate_chunk_embeddings,
)
from graph.extraction import (
    ExtractedChunk,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionResult,
    ParagraphChunkExtractor,
    validate_extraction_result,
)
from graph.graphrag import GraphRAGExtractor, ontology_graph_schema
from graph.guard import (
    ALLOWED_DOCUMENTS_PARAMETER,
    PROVENANCE_FIELDS,
    provenance_where,
    record_has_provenance,
)
from graph.ontology import (
    ENTITY_TYPES,
    RELATIONSHIP_TYPES,
    UnknownEntityTypeError,
    UnknownRelationshipTypeError,
    validate_entity_type,
    validate_relationship_type,
)
from graph.pipeline import extract_document_to_graph, get_embedding_adapter, get_extraction_adapter
from graph.schema import chunk_vector_index_statement, graph_setup_statements
from graph.writer import (
    CHUNK_DOCUMENT_RELATIONSHIP,
    CHUNK_ENTITY_RELATIONSHIP,
    ChunkNodeMissingError,
    DocumentNodeMissingError,
    MissingProvenanceError,
    document_provenance,
    replace_document_chunks,
    replace_document_entities,
    upsert_document,
)
from integrations.models import DriveConnection, SourceDocument, SourceDocumentContent


class OntologyGuardTests(SimpleTestCase):
    def test_every_declared_entity_type_is_accepted(self):
        for entity_type in ENTITY_TYPES:
            validate_entity_type(entity_type)  # must not raise

    def test_undeclared_entity_type_is_rejected(self):
        with self.assertRaises(UnknownEntityTypeError):
            validate_entity_type("SecretAgent")

    def test_every_declared_relationship_type_is_accepted(self):
        for relationship_type in RELATIONSHIP_TYPES:
            validate_relationship_type(relationship_type)  # must not raise

    def test_undeclared_relationship_type_is_rejected(self):
        with self.assertRaises(UnknownRelationshipTypeError):
            validate_relationship_type("secretly_influences")


class GraphDriverTests(SimpleTestCase):
    def tearDown(self):
        graph_db._driver = None

    @patch("graph.db.GraphDatabase")
    def test_driver_is_created_once_and_reused(self, mock_graph_database):
        graph_db._driver = None
        mock_graph_database.driver.return_value = MagicMock()

        first = graph_db.get_driver()
        second = graph_db.get_driver()

        self.assertIs(first, second)
        mock_graph_database.driver.assert_called_once()

    @patch("graph.db.GraphDatabase")
    def test_close_driver_closes_and_forgets_the_instance(self, mock_graph_database):
        graph_db._driver = None
        created = MagicMock()
        mock_graph_database.driver.return_value = created

        graph_db.get_driver()
        graph_db.close_driver()

        created.close.assert_called_once()
        self.assertIsNone(graph_db._driver)

    @patch("graph.db.GraphDatabase")
    def test_write_transaction_yields_the_explicit_transaction(self, mock_graph_database):
        graph_db._driver = None
        db_session = MagicMock()
        transaction = MagicMock()
        session_context = mock_graph_database.driver.return_value.session.return_value
        session_context.__enter__.return_value = db_session
        db_session.begin_transaction.return_value.__enter__.return_value = transaction

        with graph_db.write_transaction() as actual_transaction:
            self.assertIs(actual_transaction, transaction)

        db_session.begin_transaction.assert_called_once_with()


class GraphSetupCommandTests(SimpleTestCase):
    @override_settings(
        GRAPH_CHUNK_VECTOR_INDEX_NAME="test_chunk_embedding_vector",
        GRAPH_CHUNK_EMBEDDING_DIMENSIONS=384,
        GRAPH_CHUNK_VECTOR_SIMILARITY="cosine",
    )
    @patch("graph.management.commands.graph_setup.session")
    def test_applies_every_declared_constraint(self, mock_session_ctx):
        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__.return_value = mock_session

        call_command("graph_setup")

        actual_statements = [call.args[0] for call in mock_session.run.call_args_list]
        self.assertEqual(actual_statements, graph_setup_statements())


class VectorIndexSchemaTests(SimpleTestCase):
    def test_chunk_vector_index_statement_uses_configured_shape(self):
        statement = chunk_vector_index_statement(
            index_name="chunk_embedding_vector",
            dimensions=384,
            similarity_function="cosine",
        )

        self.assertIn("CREATE VECTOR INDEX chunk_embedding_vector IF NOT EXISTS", statement)
        self.assertIn("FOR (c:Chunk) ON (c.embedding)", statement)
        self.assertIn("`vector.dimensions`: 384", statement)
        self.assertIn("`vector.similarity_function`: 'cosine'", statement)

    def test_chunk_vector_index_rejects_unsafe_identifier(self):
        with self.assertRaises(ValueError):
            chunk_vector_index_statement(
                index_name="chunk embedding",
                dimensions=384,
                similarity_function="cosine",
            )


class ChunkEmbeddingTests(SimpleTestCase):
    def test_noop_adapter_leaves_embeddings_unset(self):
        chunks = (ExtractedChunk(index=0, text="text"),)

        self.assertEqual(NoOpEmbeddingAdapter().embed_chunks(chunks), ())
        self.assertEqual(NoOpEmbeddingAdapter().embed_query("question"), ())

    @staticmethod
    def _response(*vectors):
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=list(vector))
                for index, vector in enumerate(vectors)
            ]
        )

    def test_openrouter_adapter_batches_chunks_and_preserves_chunk_indices(self):
        client = MagicMock()
        client.embeddings.create.side_effect = [
            self._response((0.1, 0.2), (0.3, 0.4)),
            self._response((0.5, 0.6)),
        ]
        adapter = OpenRouterEmbeddingAdapter(
            client=client,
            model="openai/text-embedding-3-small",
            dimensions=2,
            batch_size=2,
        )
        chunks = tuple(ExtractedChunk(index=index + 4, text=f"text {index}") for index in range(3))

        result = adapter.embed_chunks(chunks)

        self.assertEqual([embedding.chunk_index for embedding in result], [4, 5, 6])
        self.assertEqual(
            [embedding.vector for embedding in result],
            [(0.1, 0.2), (0.3, 0.4), (0.5, 0.6)],
        )
        self.assertEqual(client.embeddings.create.call_count, 2)
        self.assertEqual(
            client.embeddings.create.call_args_list[0].kwargs,
            {
                "model": "openai/text-embedding-3-small",
                "input": ["text 0", "text 1"],
                "dimensions": 2,
                "encoding_format": "float",
            },
        )

    def test_openrouter_query_embedding_uses_the_same_model_and_dimensions(self):
        client = MagicMock()
        client.embeddings.create.return_value = self._response((0.1, 0.2, 0.3))
        adapter = OpenRouterEmbeddingAdapter(
            client=client,
            model="embedding-model",
            dimensions=3,
            batch_size=4,
        )

        self.assertEqual(adapter.embed_query("Who owns Atlas?"), (0.1, 0.2, 0.3))
        self.assertEqual(client.embeddings.create.call_args.kwargs["input"], ["Who owns Atlas?"])

    def test_openrouter_adapter_restores_provider_index_order(self):
        client = MagicMock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.3, 0.4]),
                SimpleNamespace(index=0, embedding=[0.1, 0.2]),
            ]
        )
        adapter = OpenRouterEmbeddingAdapter(
            client=client,
            model="embedding-model",
            dimensions=2,
            batch_size=2,
        )

        result = adapter.embed_chunks(
            (ExtractedChunk(index=10, text="first"), ExtractedChunk(index=11, text="second"))
        )

        self.assertEqual([embedding.vector for embedding in result], [(0.1, 0.2), (0.3, 0.4)])

    def test_openrouter_adapter_rejects_malformed_provider_vectors(self):
        invalid_responses = (
            SimpleNamespace(data=[]),
            SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[0.1, 0.2])]),
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1])]),
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1, float("nan")])]),
        )
        for response in invalid_responses:
            with self.subTest(response=response):
                client = MagicMock()
                client.embeddings.create.return_value = response
                adapter = OpenRouterEmbeddingAdapter(
                    client=client,
                    model="embedding-model",
                    dimensions=2,
                    batch_size=1,
                )
                with self.assertRaises(EmbeddingResponseError):
                    adapter.embed_query("question")

    @override_settings(GRAPH_EMBEDDING_PROVIDER="none")
    def test_builder_returns_noop_when_embeddings_are_disabled(self):
        self.assertIsInstance(build_embedding_adapter(), NoOpEmbeddingAdapter)

    @override_settings(
        GRAPH_EMBEDDING_PROVIDER="openrouter",
        OPENROUTER_API_KEY="test-key",
        OPENROUTER_BASE_URL="https://openrouter.ai/api/v1",
        OPENROUTER_SITE_URL="https://knowledge.example.com",
        OPENROUTER_APP_NAME="Knowledge Graph",
        OPENROUTER_REQUEST_TIMEOUT_SECONDS=12.5,
        OPENROUTER_EMBEDDING_MODEL="embedding-model",
        GRAPH_CHUNK_EMBEDDING_DIMENSIONS=3,
        GRAPH_EMBEDDING_BATCH_SIZE=8,
    )
    @patch("graph.embeddings.OpenAI")
    def test_builder_configures_openrouter_without_making_a_request(self, mock_openai):
        adapter = build_embedding_adapter()

        self.assertIsInstance(adapter, OpenRouterEmbeddingAdapter)
        mock_openai.assert_called_once_with(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            timeout=12.5,
            max_retries=0,
            default_headers={
                "X-OpenRouter-Title": "Knowledge Graph",
                "HTTP-Referer": "https://knowledge.example.com",
            },
        )
        mock_openai.return_value.embeddings.create.assert_not_called()

    def test_valid_embeddings_are_keyed_by_chunk_index(self):
        chunks = (
            ExtractedChunk(index=0, text="first"),
            ExtractedChunk(index=1, text="second"),
        )
        embeddings = (
            ChunkEmbedding(chunk_index=0, vector=(0.1, 0.2, 0.3)),
            ChunkEmbedding(chunk_index=1, vector=(0.4, 0.5, 0.6)),
        )

        vectors = validate_chunk_embeddings(chunks, embeddings, dimensions=3)

        self.assertEqual(vectors, {0: [0.1, 0.2, 0.3], 1: [0.4, 0.5, 0.6]})

    def test_embedding_count_must_match_chunk_count(self):
        chunks = (ExtractedChunk(index=0, text="first"), ExtractedChunk(index=1, text="second"))
        embeddings = (ChunkEmbedding(chunk_index=0, vector=(0.1, 0.2, 0.3)),)

        with self.assertRaises(ChunkEmbeddingValidationError):
            validate_chunk_embeddings(chunks, embeddings, dimensions=3)

    def test_embedding_dimensions_must_match_configured_dimensions(self):
        chunks = (ExtractedChunk(index=0, text="first"),)
        embeddings = (ChunkEmbedding(chunk_index=0, vector=(0.1, 0.2)),)

        with self.assertRaises(ChunkEmbeddingValidationError):
            validate_chunk_embeddings(chunks, embeddings, dimensions=3)


class ParagraphChunkExtractorTests(SimpleTestCase):
    def test_paragraphs_become_sequential_chunks(self):
        result = ParagraphChunkExtractor().extract("First block.\n\nSecond block.\n\n\n\nThird.")

        self.assertEqual(
            result.chunks,
            (
                ExtractedChunk(index=0, text="First block."),
                ExtractedChunk(index=1, text="Second block."),
                ExtractedChunk(index=2, text="Third."),
            ),
        )
        self.assertEqual(result.entities, ())
        self.assertEqual(result.relationships, ())

    def test_whitespace_only_input_yields_no_chunks(self):
        result = ParagraphChunkExtractor().extract("  \n\n   \n\n")

        self.assertEqual(result.chunks, ())

    def test_oversized_paragraph_is_split_into_bounded_overlapping_chunks(self):
        extractor = ParagraphChunkExtractor(max_chars=10, overlap_chars=2)
        result = extractor.extract("abcdefghijklmnopqrst")

        self.assertEqual(
            result.chunks,
            (
                ExtractedChunk(index=0, text="abcdefghij"),
                ExtractedChunk(index=1, text="ijklmnopqr"),
                ExtractedChunk(index=2, text="qrst"),
            ),
        )
        self.assertTrue(all(len(chunk.text) <= 10 for chunk in result.chunks))

    def test_oversized_csv_text_prefers_row_boundaries(self):
        csv_text = "header,value\\nfirst,123\\nsecond,456\\nthird,789"

        result = ParagraphChunkExtractor(max_chars=18, overlap_chars=3).extract(csv_text)

        self.assertGreater(len(result.chunks), 1)
        self.assertEqual([chunk.index for chunk in result.chunks], list(range(len(result.chunks))))
        self.assertTrue(all(len(chunk.text) <= 18 for chunk in result.chunks))


class ExtractionResultValidationTests(SimpleTestCase):
    def test_declared_types_pass_through(self):
        result = ExtractionResult(
            chunks=(ExtractedChunk(index=0, text="text"),),
            entities=(ExtractedEntity(entity_type="Person", name="Ada", chunk_index=0),),
            relationships=(
                ExtractedRelationship(
                    relationship_type="works_on",
                    source_name="Ada",
                    target_name="Engine",
                    chunk_index=0,
                ),
            ),
        )

        self.assertIs(validate_extraction_result(result), result)

    def test_undeclared_entity_type_fails(self):
        result = ExtractionResult(
            entities=(ExtractedEntity(entity_type="Wizard", name="Merlin", chunk_index=0),),
        )

        with self.assertRaises(UnknownEntityTypeError):
            validate_extraction_result(result)

    def test_undeclared_relationship_type_fails(self):
        result = ExtractionResult(
            relationships=(
                ExtractedRelationship(
                    relationship_type="enchants",
                    source_name="Merlin",
                    target_name="Sword",
                    chunk_index=0,
                ),
            ),
        )

        with self.assertRaises(UnknownRelationshipTypeError):
            validate_extraction_result(result)


def _document(**overrides) -> SourceDocument:
    fields = {
        "pk": 7,
        "connection_id": 3,
        "drive_file_id": "file-1",
        "source_permissions_version": "a" * 64,
        "title": "Notes",
        "mime_type": "text/plain",
        "drive_url": "https://drive.example/file-1",
    }
    fields.update(overrides)
    return SourceDocument(**fields)


class DocumentProvenanceTests(SimpleTestCase):
    def test_complete_identity_yields_provenance(self):
        provenance = document_provenance(_document())

        self.assertEqual(
            provenance,
            {
                "source_document_id": 7,
                "connection_id": 3,
                "drive_file_id": "file-1",
                "source_permissions_version": "a" * 64,
            },
        )

    def test_each_missing_identity_field_is_refused(self):
        for overrides in ({"pk": None}, {"connection_id": None}, {"drive_file_id": ""}):
            with self.assertRaises(MissingProvenanceError):
                document_provenance(_document(**overrides))

    def test_blank_permissions_version_is_carried_not_refused(self):
        provenance = document_provenance(_document(source_permissions_version=""))

        self.assertEqual(provenance["source_permissions_version"], "")


class WriterTests(SimpleTestCase):
    def test_chunk_document_relationship_is_declared_in_the_ontology(self):
        self.assertIn(CHUNK_DOCUMENT_RELATIONSHIP, RELATIONSHIP_TYPES)

    def test_upsert_document_writes_all_provenance_fields(self):
        db_session = MagicMock()

        upsert_document(db_session, _document())

        kwargs = db_session.run.call_args.kwargs
        self.assertEqual(kwargs["source_document_id"], 7)
        self.assertEqual(kwargs["connection_id"], 3)
        self.assertEqual(kwargs["drive_file_id"], "file-1")
        self.assertEqual(kwargs["source_permissions_version"], "a" * 64)
        self.assertEqual(kwargs["content_hash"], "")

    def test_upsert_refuses_documents_without_identity(self):
        db_session = MagicMock()

        with self.assertRaises(MissingProvenanceError):
            upsert_document(db_session, _document(drive_file_id=""))

        db_session.run.assert_not_called()

    def test_replace_chunks_fails_loudly_when_document_node_is_absent(self):
        db_session = MagicMock()
        db_session.run.return_value.single.return_value = None

        with self.assertRaises(DocumentNodeMissingError):
            replace_document_chunks(
                db_session, _document(), (ExtractedChunk(index=0, text="text"),)
            )

    def test_replace_chunks_deletes_then_creates_with_provenance(self):
        db_session = MagicMock()
        chunks = (
            ExtractedChunk(index=0, text="first"),
            ExtractedChunk(index=1, text="second"),
        )

        written = replace_document_chunks(db_session, _document(), chunks)

        self.assertEqual(written, 2)
        calls = db_session.run.call_args_list
        # existence check, delete, then one batched create for all chunks
        self.assertEqual(len(calls), 3)
        self.assertIn("DETACH DELETE", calls[1].args[0])
        create_call = calls[2]
        self.assertIn("UNWIND $chunks", create_call.args[0])
        self.assertIn("CREATE (c:Chunk", create_call.args[0])
        self.assertIn(f":{CHUNK_DOCUMENT_RELATIONSHIP}", create_call.args[0])
        self.assertIn("r.source_document_id", create_call.args[0])
        kwargs = create_call.kwargs
        for field in PROVENANCE_FIELDS:
            self.assertIsNotNone(kwargs[field])
        for row, chunk in zip(kwargs["chunks"], chunks, strict=True):
            self.assertEqual(row["chunk_id"], f"7:{chunk.index}")
            self.assertEqual(row["text"], chunk.text)
            self.assertIsNone(row["embedding"])

    def test_replace_chunks_can_write_embeddings(self):
        db_session = MagicMock()
        chunks = (ExtractedChunk(index=0, text="first"),)
        embeddings = (ChunkEmbedding(chunk_index=0, vector=(0.1, 0.2, 0.3)),)

        written = replace_document_chunks(
            db_session,
            _document(),
            chunks,
            chunk_embeddings=embeddings,
            embedding_dimensions=3,
        )

        self.assertEqual(written, 1)
        create_call = db_session.run.call_args_list[-1]
        self.assertIn("embedding: chunk.embedding", create_call.args[0])
        self.assertEqual(create_call.kwargs["chunks"][0]["embedding"], [0.1, 0.2, 0.3])

    def test_replace_chunks_refuses_malformed_embeddings_before_writing(self):
        db_session = MagicMock()
        chunks = (ExtractedChunk(index=0, text="first"),)
        embeddings = (ChunkEmbedding(chunk_index=0, vector=(0.1, 0.2)),)

        with self.assertRaises(ChunkEmbeddingValidationError):
            replace_document_chunks(
                db_session,
                _document(),
                chunks,
                chunk_embeddings=embeddings,
                embedding_dimensions=3,
            )

        db_session.run.assert_not_called()


class OntologyGraphSchemaTests(SimpleTestCase):
    def test_schema_mirrors_the_declared_ontology_and_is_closed(self):
        schema = ontology_graph_schema()

        self.assertEqual({n.label for n in schema.node_types}, set(ENTITY_TYPES))
        self.assertEqual({r.label for r in schema.relationship_types}, set(RELATIONSHIP_TYPES))
        self.assertFalse(schema.additional_node_types)
        self.assertFalse(schema.additional_relationship_types)


def _fake_llm(payload: str):
    from neo4j_graphrag.llm import LLMInterface, LLMResponse

    class FakeLLM(LLMInterface):
        def __init__(self):
            super().__init__(model_name="fake")

        def invoke(self, input, message_history=None, system_instruction=None):
            return LLMResponse(content=payload)

        async def ainvoke(self, input, message_history=None, system_instruction=None):
            return LLMResponse(content=payload)

    return FakeLLM()


class GraphRAGExtractorTests(SimpleTestCase):
    # Exercises the real neo4j-graphrag extraction component (prompting,
    # JSON parsing, pydantic validation) with only the LLM call faked.
    def test_llm_output_maps_to_boundary_dataclasses_with_chunk_provenance(self):
        payload = (
            '{"nodes": ['
            '{"id": "0", "label": "Person", "properties": {"name": "Ada Lovelace"}},'
            '{"id": "1", "label": "Project", "properties": {"name": "Analytical Engine"}}'
            '], "relationships": ['
            '{"type": "works_on", "start_node_id": "0", "end_node_id": "1", "properties": {}}'
            "]}"
        )
        extractor = GraphRAGExtractor(llm=_fake_llm(payload))

        result = extractor.extract("Ada Lovelace works on the Analytical Engine.")

        self.assertEqual(len(result.chunks), 1)
        self.assertEqual(
            {(e.entity_type, e.name, e.chunk_index) for e in result.entities},
            {("Person", "Ada Lovelace", 0), ("Project", "Analytical Engine", 0)},
        )
        self.assertEqual(
            result.relationships,
            (
                ExtractedRelationship(
                    relationship_type="works_on",
                    source_name="Ada Lovelace",
                    target_name="Analytical Engine",
                    chunk_index=0,
                    source_type="Person",
                    target_type="Project",
                ),
            ),
        )

    def test_relationship_with_undeclared_node_id_is_dropped(self):
        payload = (
            '{"nodes": [{"id": "0", "label": "Person", "properties": {"name": "Ada"}}],'
            '"relationships": ['
            '{"type": "works_on", "start_node_id": "0", "end_node_id": "99", "properties": {}}'
            "]}"
        )
        extractor = GraphRAGExtractor(llm=_fake_llm(payload))

        result = extractor.extract("Some text.")

        self.assertEqual(len(result.entities), 1)
        self.assertEqual(result.relationships, ())

    def test_each_paragraph_is_extracted_with_its_own_chunk_index(self):
        payload = (
            '{"nodes": [{"id": "0", "label": "Topic", "properties": {"name": "Safety"}}],'
            '"relationships": []}'
        )
        extractor = GraphRAGExtractor(llm=_fake_llm(payload))

        result = extractor.extract("Paragraph one.\n\nParagraph two.")

        self.assertEqual([e.chunk_index for e in result.entities], [0, 1])

    def test_llm_extraction_uses_bounded_chunks(self):
        payload = '{"nodes": [], "relationships": []}'
        extractor = GraphRAGExtractor(
            llm=_fake_llm(payload),
            chunker=ParagraphChunkExtractor(max_chars=10, overlap_chars=2),
        )

        result = extractor.extract("abcdefghijklmnopqrst")

        self.assertEqual([chunk.index for chunk in result.chunks], [0, 1, 2])
        self.assertTrue(all(len(chunk.text) <= 10 for chunk in result.chunks))

    @override_settings(
        GRAPH_EXTRACTION_MODEL="test-model",
        OPENROUTER_BASE_URL="https://openrouter.ai/api/v1",
        OPENROUTER_API_KEY="test-key",
    )
    @patch("neo4j_graphrag.llm.OpenAILLM")
    def test_build_extractor_uses_openrouter_settings(self, mock_llm):
        from graph.graphrag import build_graphrag_extractor

        extractor = build_graphrag_extractor()

        self.assertIsInstance(extractor, GraphRAGExtractor)
        mock_llm.assert_called_once_with(
            model_name="test-model",
            model_params={"temperature": 0.0, "response_format": {"type": "json_object"}},
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
        )


class ExtractionEngineSelectionTests(SimpleTestCase):
    @override_settings(GRAPH_EXTRACTION_ENGINE="paragraph")
    def test_paragraph_engine_is_the_deterministic_baseline(self):
        self.assertIsInstance(get_extraction_adapter(), ParagraphChunkExtractor)

    @override_settings(GRAPH_EXTRACTION_ENGINE="neo4j_graphrag")
    @patch("graph.graphrag.build_graphrag_extractor")
    def test_graphrag_engine_is_built_when_selected(self, mock_build):
        sentinel = object()
        mock_build.return_value = sentinel

        self.assertIs(get_extraction_adapter(), sentinel)

    @patch("graph.pipeline.build_embedding_adapter")
    def test_embedding_engine_uses_the_shared_builder(self, mock_build):
        sentinel = object()
        mock_build.return_value = sentinel

        self.assertIs(get_embedding_adapter(), sentinel)


class ReplaceDocumentEntitiesTests(SimpleTestCase):
    def _session(self, anchored=1):
        # The batched entity write reports how many mentions found their
        # anchoring chunk; tests set it to the mention count they pass in.
        db_session = MagicMock()
        db_session.run.return_value.single.return_value = {"n": anchored}
        return db_session

    def test_undeclared_entity_type_is_rejected_before_any_write(self):
        db_session = self._session()
        entities = (ExtractedEntity(entity_type="Wizard", name="Merlin", chunk_index=0),)

        with self.assertRaises(UnknownEntityTypeError):
            replace_document_entities(db_session, _document(), entities, ())

        db_session.run.assert_not_called()

    def test_undeclared_relationship_type_is_rejected_before_any_write(self):
        db_session = self._session()
        relationships = (
            ExtractedRelationship(
                relationship_type="enchants",
                source_name="A",
                target_name="B",
                chunk_index=0,
            ),
        )

        with self.assertRaises(UnknownRelationshipTypeError):
            replace_document_entities(db_session, _document(), (), relationships)

        db_session.run.assert_not_called()

    def test_entities_are_written_with_provenance_and_a_mention_edge(self):
        db_session = self._session()
        entities = (ExtractedEntity(entity_type="Person", name="Ada Lovelace", chunk_index=0),)

        counts = replace_document_entities(db_session, _document(), entities, ())

        self.assertEqual(counts, {"entities": 1, "relationships": 0, "relationships_skipped": 0})
        delete_call, entity_call = db_session.run.call_args_list
        self.assertIn("DETACH DELETE e", delete_call.args[0])
        self.assertIn("UNWIND $entities", entity_call.args[0])
        self.assertIn(f":{CHUNK_ENTITY_RELATIONSHIP}", entity_call.args[0])
        self.assertIn("m.source_document_id", entity_call.args[0])
        kwargs = entity_call.kwargs
        self.assertEqual(kwargs["entities"][0]["entity_id"], "7:Person:ada lovelace")
        self.assertEqual(kwargs["entities"][0]["chunk_id"], "7:0")
        for field in PROVENANCE_FIELDS:
            self.assertIsNotNone(kwargs[field])

    def test_missing_chunk_anchor_fails_loudly(self):
        db_session = self._session(anchored=0)
        entities = (ExtractedEntity(entity_type="Person", name="Ada", chunk_index=3),)

        with self.assertRaises(ChunkNodeMissingError):
            replace_document_entities(db_session, _document(), entities, ())

    def test_same_entity_in_two_chunks_counts_once_with_two_mentions(self):
        db_session = self._session(anchored=2)
        entities = (
            ExtractedEntity(entity_type="Person", name="Ada", chunk_index=0),
            ExtractedEntity(entity_type="Person", name="Ada", chunk_index=1),
        )

        counts = replace_document_entities(db_session, _document(), entities, ())

        self.assertEqual(counts["entities"], 1)
        # delete + one batched write covering both mentions
        self.assertEqual(db_session.run.call_count, 2)
        mention_rows = db_session.run.call_args_list[-1].kwargs["entities"]
        self.assertEqual(len(mention_rows), 2)

    def test_resolvable_relationship_is_written_with_provenance(self):
        db_session = self._session(anchored=2)
        entities = (
            ExtractedEntity(entity_type="Person", name="Ada", chunk_index=0),
            ExtractedEntity(entity_type="Project", name="Engine", chunk_index=0),
        )
        relationships = (
            ExtractedRelationship(
                relationship_type="works_on",
                source_name="Ada",
                target_name="Engine",
                chunk_index=0,
            ),
        )

        counts = replace_document_entities(db_session, _document(), entities, relationships)

        self.assertEqual(counts["relationships"], 1)
        self.assertEqual(counts["relationships_skipped"], 0)
        relationship_call = db_session.run.call_args_list[-1]
        self.assertIn("[r:works_on]", relationship_call.args[0])
        rows = relationship_call.kwargs["relationships"]
        self.assertEqual(
            rows, [{"source_id": "7:Person:ada", "target_id": "7:Project:engine", "chunk_index": 0}]
        )
        for field in PROVENANCE_FIELDS:
            self.assertIsNotNone(relationship_call.kwargs[field])

    def test_unresolvable_or_ambiguous_relationships_are_counted_and_skipped(self):
        db_session = self._session(anchored=2)
        entities = (
            # Same name under two types → ambiguous endpoint.
            ExtractedEntity(entity_type="Person", name="Mercury", chunk_index=0),
            ExtractedEntity(entity_type="Topic", name="Mercury", chunk_index=0),
        )
        relationships = (
            ExtractedRelationship(
                relationship_type="related_to",
                source_name="Mercury",
                target_name="Mercury",
                chunk_index=0,
            ),
            ExtractedRelationship(
                relationship_type="related_to",
                source_name="Nobody",
                target_name="Mercury",
                chunk_index=0,
            ),
        )

        counts = replace_document_entities(db_session, _document(), entities, relationships)

        self.assertEqual(counts["relationships"], 0)
        self.assertEqual(counts["relationships_skipped"], 2)

    def test_endpoint_types_disambiguate_same_named_entities(self):
        db_session = self._session(anchored=2)
        entities = (
            ExtractedEntity(entity_type="Person", name="Mercury", chunk_index=0),
            ExtractedEntity(entity_type="Topic", name="Mercury", chunk_index=0),
        )
        relationships = (
            ExtractedRelationship(
                relationship_type="related_to",
                source_name="Mercury",
                target_name="Mercury",
                chunk_index=0,
                # The engine knows which "Mercury" each endpoint is — the
                # writer must not drop the edge as ambiguous.
                source_type="Person",
                target_type="Topic",
            ),
        )

        counts = replace_document_entities(db_session, _document(), entities, relationships)

        self.assertEqual(counts["relationships"], 1)
        self.assertEqual(counts["relationships_skipped"], 0)
        rows = db_session.run.call_args_list[-1].kwargs["relationships"]
        self.assertEqual(rows[0]["source_id"], "7:Person:mercury")
        self.assertEqual(rows[0]["target_id"], "7:Topic:mercury")


class ExtractionPipelineTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-root",
        )
        self.document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="file-1",
            title="Notes",
            mime_type="application/vnd.google-apps.document",
            source_permissions_version="a" * 64,
        )

    def _store_content(
        self, data: bytes, exported_mime_type: str = "text/plain", content_hash: str = "hash"
    ):
        self.document.content_hash = content_hash
        self.document.save(update_fields=["content_hash"])
        return SourceDocumentContent.objects.create(
            source_document=self.document,
            content=data,
            exported_mime_type=exported_mime_type,
            content_hash=content_hash,
        )

    def test_document_without_stored_content_is_skipped(self):
        result = extract_document_to_graph(self.document.pk)

        self.assertEqual(
            result,
            {"source_document_id": self.document.pk, "status": "skipped_no_content"},
        )

    def test_non_text_content_is_skipped(self):
        self._store_content(b"image bytes", exported_mime_type="image/png")

        result = extract_document_to_graph(self.document.pk)

        self.assertEqual(result["status"], "skipped_unsupported_mime_type")

    @override_settings(GRAPH_EXTRACTION_ENGINE="paragraph")
    @patch("graph.pipeline.write_transaction")
    @patch("graph.pipeline.PdfReader")
    def test_pdf_text_is_extracted_and_written(self, mock_reader, mock_transaction_ctx):
        first_page = MagicMock()
        first_page.extract_text.return_value = "First PDF paragraph."
        second_page = MagicMock()
        second_page.extract_text.return_value = "Second PDF paragraph."
        mock_reader.return_value.pages = [first_page, second_page]
        db_transaction = MagicMock()
        mock_transaction_ctx.return_value.__enter__.return_value = db_transaction
        self._store_content(b"%PDF-1.7 test", exported_mime_type="application/pdf")

        result = extract_document_to_graph(self.document.pk)

        self.assertEqual(result["status"], "extracted")
        self.assertEqual(result["chunks"], 2)
        mock_reader.assert_called_once()
        self.assertFalse(mock_reader.call_args.kwargs["strict"])

    @patch("graph.pipeline.PdfReader")
    def test_pdf_without_extractable_text_is_skipped(self, mock_reader):
        page = MagicMock()
        page.extract_text.return_value = "  "
        mock_reader.return_value.pages = [page]
        self._store_content(b"%PDF-1.7 scan", exported_mime_type="application/pdf")

        result = extract_document_to_graph(self.document.pk)

        self.assertEqual(result["status"], "skipped_pdf_no_extractable_text")

    def test_undecodable_content_is_skipped_without_leaking_bytes(self):
        self._store_content(b"\xff\xfe\xfa broken")

        result = extract_document_to_graph(self.document.pk)

        self.assertEqual(result["status"], "skipped_decode_error")

    @override_settings(GRAPH_EXTRACTION_ENGINE="paragraph")
    @patch("graph.pipeline.write_transaction")
    def test_text_content_is_written_as_document_and_chunks(self, mock_transaction_ctx):
        db_transaction = MagicMock()
        mock_transaction_ctx.return_value.__enter__.return_value = db_transaction
        self._store_content(b"First paragraph.\n\nSecond paragraph.")

        result = extract_document_to_graph(self.document.pk)

        self.assertEqual(
            result,
            {
                "source_document_id": self.document.pk,
                "status": "extracted",
                "chunks": 2,
                "entities": 0,
                "relationships": 0,
                "relationships_skipped": 0,
            },
        )
        mock_transaction_ctx.assert_called_once_with()
        statements = [call.args[0] for call in db_transaction.run.call_args_list]
        self.assertTrue(any("MERGE (d:Document" in statement for statement in statements))
        create_calls = [
            call for call in db_transaction.run.call_args_list if "CREATE (c:Chunk" in call.args[0]
        ]
        self.assertEqual(len(create_calls), 1)
        create_call = create_calls[0]
        self.assertEqual(len(create_call.kwargs["chunks"]), 2)
        self.assertEqual(create_call.kwargs["source_document_id"], self.document.pk)
        self.assertEqual(create_call.kwargs["drive_file_id"], "file-1")

    @override_settings(GRAPH_EXTRACTION_ENGINE="paragraph")
    @patch("graph.pipeline.write_transaction")
    @patch("graph.pipeline.get_extraction_adapter")
    def test_content_changed_during_extraction_never_replaces_the_newer_graph(
        self, mock_adapter, mock_transaction_ctx
    ):
        self._store_content(b"old content", content_hash="old-hash")

        def replace_content_while_extracting(_text):
            SourceDocumentContent.objects.filter(source_document=self.document).update(
                content=b"new content", content_hash="new-hash"
            )
            SourceDocument.objects.filter(pk=self.document.pk).update(content_hash="new-hash")
            return ExtractionResult(chunks=(ExtractedChunk(index=0, text="old content"),))

        mock_adapter.return_value.extract.side_effect = replace_content_while_extracting

        result = extract_document_to_graph(self.document.pk, "old-hash")

        self.assertEqual(result["status"], "skipped_stale_content_version")
        mock_transaction_ctx.assert_not_called()


class RetrievalGuardTests(SimpleTestCase):
    def test_where_fragment_requires_every_provenance_field_and_allowlist(self):
        fragment = provenance_where("c")

        for field in PROVENANCE_FIELDS:
            self.assertIn(f"c.{field} IS NOT NULL", fragment)
        self.assertIn(f"c.source_document_id IN ${ALLOWED_DOCUMENTS_PARAMETER}", fragment)

    def test_non_identifier_alias_is_rejected_at_the_interpolation_point(self):
        for alias in ("", "c ", "c.x", "1c", "c) OR true //"):
            with self.assertRaises(ValueError):
                provenance_where(alias)

    def test_record_with_full_provenance_passes(self):
        properties = {field: "value" for field in PROVENANCE_FIELDS}

        self.assertTrue(record_has_provenance(properties))

    def test_record_missing_any_provenance_field_fails(self):
        for missing in PROVENANCE_FIELDS:
            properties = {field: "value" for field in PROVENANCE_FIELDS if field != missing}
            self.assertFalse(record_has_provenance(properties))

    def test_record_with_null_provenance_field_fails(self):
        properties = {field: "value" for field in PROVENANCE_FIELDS}
        properties["drive_file_id"] = None

        self.assertFalse(record_has_provenance(properties))
