from django.test import TestCase, override_settings
from django.utils import timezone

from authorization.client import PermissionTuple
from authorization.identifiers import document_object_id, user_object_id
from authorization.lookup import allowed_source_document_ids
from integrations.drive.user_oauth import GOOGLE_ISSUERS, REQUIRED_SCOPES
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
)
from retrieval.answers import GeneratedAnswer
from retrieval.services import answer_query
from retrieval.types import RetrievalEvidence, RetrievedChunk


class DirectSpiceDB:
    def __init__(self, direct_tuple):
        self.direct_tuple = direct_tuple

    def read_oauth_viewer_tuples(self, prefix, user_id, *, revision=""):
        return {self.direct_tuple}

    def lookup_documents(self, user_id):
        raise AssertionError("combined view lookup must not run in per-user mode")


class Retriever:
    def __init__(self, evidence, *, during_retrieval=None):
        self.evidence = evidence
        self.during_retrieval = during_retrieval
        self.calls = []

    def retrieve(self, question, allowed_ids):
        self.calls.append((question, allowed_ids))
        if self.during_retrieval:
            self.during_retrieval()
        return self.evidence


class Generator:
    def __init__(self):
        self.calls = []

    def generate(self, question, context):
        self.calls.append((question, context))
        return GeneratedAnswer(answer="Allowed answer.", supported=True)


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
    GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS=1800,
)
class PerUserQuerySecurityTests(TestCase):
    email = "reader@example.com"

    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="root",
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )
        # Delegated/global SpiceDB evidence is intentionally absent. Per-user
        # retrieval must use the coarse content gate plus user-specific proof.
        self.document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="doc-1",
            title="Secret",
            mime_type="text/plain",
            drive_url="https://drive.google.com/file/d/doc-1",
            active_in_scope=True,
            retrieval_eligible=True,
            source_permissions_version="per-user-v1",
            content_hash="content-v1",
        )
        self.authorization = GoogleDriveAuthorization.objects.create(
            connection=self.connection,
            google_issuer=min(GOOGLE_ISSUERS),
            google_subject="subject-1",
            normalized_email=self.email,
            workspace_domain="example.com",
            encrypted_refresh_credential=b"ciphertext",
            encryption_key_version="v1",
            granted_scopes=sorted(REQUIRED_SCOPES),
            connection_generation=self.connection.authorization_generation,
            status=GoogleDriveAuthorization.Status.ACTIVE,
        )
        self.visibility = UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=self.document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            checked_at=timezone.now(),
            spicedb_revision="zed-token",
            spicedb_verified_at=timezone.now(),
        )
        direct_tuple = PermissionTuple(
            resource_type="kgm/document",
            resource_id=document_object_id(self.connection.pk, self.document.pk),
            relation="oauth_viewer",
            subject_type="kgm/user",
            subject_id=user_object_id(self.connection.pk, self.email),
        )
        self.spicedb = DirectSpiceDB(direct_tuple)
        self.evidence = RetrievalEvidence(
            chunks=(
                RetrievedChunk(
                    source_document_id=self.document.pk,
                    chunk_id=f"{self.document.pk}:0",
                    text="Per-user accessible context.",
                    content_version="content-v1",
                ),
            )
        )

    def lookup(self, email):
        return allowed_source_document_ids(email, spicedb=self.spicedb)

    def test_fresh_direct_per_user_proof_reaches_the_answer_boundary(self):
        generator = Generator()

        result = answer_query(
            "question",
            self.email,
            allowed_lookup=self.lookup,
            retriever=Retriever(self.evidence),
            answer_generator=generator,
        )

        self.assertFalse(result.refused)
        self.assertEqual(result.citations[0]["drive_file_id"], self.document.drive_file_id)
        self.assertEqual(len(generator.calls), 1)

    def test_evidence_expiry_during_neo4j_retrieval_blocks_context_export(self):
        generator = Generator()

        def expire_evidence():
            UserDocumentVisibility.objects.filter(pk=self.visibility.pk).update(
                spicedb_verified_at=None,
                spicedb_revision="",
            )

        result = answer_query(
            "question",
            self.email,
            allowed_lookup=self.lookup,
            retriever=Retriever(self.evidence, during_retrieval=expire_evidence),
            answer_generator=generator,
        )

        self.assertTrue(result.refused)
        self.assertEqual(result.citations, ())
        self.assertEqual(generator.calls, [])

    def test_postgresql_evidence_without_direct_tuple_stops_before_neo4j(self):
        self.spicedb.direct_tuple = PermissionTuple(
            resource_type="kgm/document",
            resource_id=document_object_id(self.connection.pk, self.document.pk),
            relation="oauth_viewer",
            subject_type="kgm/user",
            subject_id=user_object_id(self.connection.pk, "other@example.com"),
        )
        retriever = Retriever(self.evidence)

        result = answer_query(
            "question",
            self.email,
            allowed_lookup=self.lookup,
            retriever=retriever,
        )

        self.assertTrue(result.refused)
        self.assertEqual(retriever.calls, [])
