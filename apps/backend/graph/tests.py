from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

import graph.db as graph_db
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
from graph.pipeline import extract_document_to_graph, get_extraction_adapter
from graph.schema import CONSTRAINTS
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


class GraphSetupCommandTests(SimpleTestCase):
    @patch("graph.management.commands.graph_setup.session")
    def test_applies_every_declared_constraint(self, mock_session_ctx):
        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__.return_value = mock_session

        call_command("graph_setup")

        actual_statements = [call.args[0] for call in mock_session.run.call_args_list]
        self.assertEqual(actual_statements, CONSTRAINTS)


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
        # existence check, delete, then one create per chunk
        self.assertEqual(len(calls), 4)
        self.assertIn("DETACH DELETE", calls[1].args[0])
        for call, chunk in zip(calls[2:], chunks, strict=True):
            self.assertIn("CREATE (c:Chunk", call.args[0])
            self.assertIn(f":{CHUNK_DOCUMENT_RELATIONSHIP}", call.args[0])
            kwargs = call.kwargs
            self.assertEqual(kwargs["chunk_id"], f"7:{chunk.index}")
            self.assertEqual(kwargs["text"], chunk.text)
            for field in PROVENANCE_FIELDS:
                self.assertIsNotNone(kwargs[field])


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


class ReplaceDocumentEntitiesTests(SimpleTestCase):
    def _session(self, chunk_found=True):
        db_session = MagicMock()
        db_session.run.return_value.single.return_value = {"n": 1 if chunk_found else 0}
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
        self.assertIn(f"[:{CHUNK_ENTITY_RELATIONSHIP}]", entity_call.args[0])
        kwargs = entity_call.kwargs
        self.assertEqual(kwargs["entity_id"], "7:Person:ada lovelace")
        self.assertEqual(kwargs["chunk_id"], "7:0")
        for field in PROVENANCE_FIELDS:
            self.assertIsNotNone(kwargs[field])

    def test_missing_chunk_anchor_fails_loudly(self):
        db_session = self._session(chunk_found=False)
        entities = (ExtractedEntity(entity_type="Person", name="Ada", chunk_index=3),)

        with self.assertRaises(ChunkNodeMissingError):
            replace_document_entities(db_session, _document(), entities, ())

    def test_same_entity_in_two_chunks_counts_once_with_two_mentions(self):
        db_session = self._session()
        entities = (
            ExtractedEntity(entity_type="Person", name="Ada", chunk_index=0),
            ExtractedEntity(entity_type="Person", name="Ada", chunk_index=1),
        )

        counts = replace_document_entities(db_session, _document(), entities, ())

        self.assertEqual(counts["entities"], 1)
        # delete + one write per mention
        self.assertEqual(db_session.run.call_count, 3)

    def test_resolvable_relationship_is_written_with_provenance(self):
        db_session = self._session()
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
        self.assertEqual(relationship_call.kwargs["source_id"], "7:Person:ada")
        self.assertEqual(relationship_call.kwargs["target_id"], "7:Project:engine")
        for field in PROVENANCE_FIELDS:
            self.assertIsNotNone(relationship_call.kwargs[field])

    def test_unresolvable_or_ambiguous_relationships_are_counted_and_skipped(self):
        db_session = self._session()
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

    def _store_content(self, data: bytes, exported_mime_type: str = "text/plain"):
        return SourceDocumentContent.objects.create(
            source_document=self.document,
            content=data,
            exported_mime_type=exported_mime_type,
            content_hash="hash",
        )

    def test_document_without_stored_content_is_skipped(self):
        result = extract_document_to_graph(self.document.pk)

        self.assertEqual(
            result,
            {"source_document_id": self.document.pk, "status": "skipped_no_content"},
        )

    def test_non_text_content_is_skipped(self):
        self._store_content(b"%PDF-1.7 ...", exported_mime_type="application/pdf")

        result = extract_document_to_graph(self.document.pk)

        self.assertEqual(result["status"], "skipped_unsupported_mime_type")

    def test_undecodable_content_is_skipped_without_leaking_bytes(self):
        self._store_content(b"\xff\xfe\xfa broken")

        result = extract_document_to_graph(self.document.pk)

        self.assertEqual(result["status"], "skipped_decode_error")

    @patch("graph.pipeline.session")
    def test_text_content_is_written_as_document_and_chunks(self, mock_session_ctx):
        db_session = MagicMock()
        mock_session_ctx.return_value.__enter__.return_value = db_session
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
        statements = [call.args[0] for call in db_session.run.call_args_list]
        self.assertTrue(any("MERGE (d:Document" in statement for statement in statements))
        create_calls = [
            call for call in db_session.run.call_args_list if "CREATE (c:Chunk" in call.args[0]
        ]
        self.assertEqual(len(create_calls), 2)
        for call in create_calls:
            self.assertEqual(call.kwargs["source_document_id"], self.document.pk)
            self.assertEqual(call.kwargs["drive_file_id"], "file-1")


class RetrievalGuardTests(SimpleTestCase):
    def test_where_fragment_requires_every_provenance_field_and_allowlist(self):
        fragment = provenance_where("c")

        for field in PROVENANCE_FIELDS:
            self.assertIn(f"c.{field} IS NOT NULL", fragment)
        self.assertIn(f"c.source_document_id IN ${ALLOWED_DOCUMENTS_PARAMETER}", fragment)

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
