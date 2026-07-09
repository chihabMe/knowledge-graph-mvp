from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

import graph.db as graph_db
from graph.extraction import (
    ExtractedChunk,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionResult,
    ParagraphChunkExtractor,
    validate_extraction_result,
)
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
from graph.schema import CONSTRAINTS
from graph.writer import (
    CHUNK_DOCUMENT_RELATIONSHIP,
    DocumentNodeMissingError,
    MissingProvenanceError,
    document_provenance,
    replace_document_chunks,
    upsert_document,
)
from integrations.models import SourceDocument


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
