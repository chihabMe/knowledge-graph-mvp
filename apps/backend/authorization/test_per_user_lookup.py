import datetime
import uuid

from django.test import TestCase, override_settings
from django.utils import timezone

from authorization.client import PermissionTuple
from authorization.identifiers import connection_prefix, document_object_id, user_object_id
from authorization.lookup import allowed_source_document_ids, fresh_authorized_documents
from integrations.drive.user_oauth import GOOGLE_ISSUERS, REQUIRED_SCOPES
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
)


class DirectTupleSpiceDB:
    def __init__(self, tuples=(), *, fail=False, legacy_resources=()):
        self.tuples = set(tuples)
        self.fail = fail
        self.legacy_resources = tuple(legacy_resources)
        self.direct_calls = []
        self.lookup_calls = []

    def read_oauth_viewer_tuples(self, prefix, user_id, *, revision=""):
        self.direct_calls.append((prefix, user_id, revision))
        if self.fail:
            raise TimeoutError
        return set(self.tuples)

    def lookup_documents(self, user_id):
        self.lookup_calls.append(user_id)
        return self.legacy_resources


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
    GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS=1800,
)
class PerUserAllowedDocumentLookupTests(TestCase):
    email = "reader@example.com"

    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="root",
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )
        self.document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="doc-1",
            title="Secret",
            mime_type="text/plain",
            active_in_scope=True,
            retrieval_eligible=True,
            source_permissions_version="per-user-v1",
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
            connected_at=timezone.now(),
        )

    def direct_tuple(self, *, email=None, document=None):
        email = email or self.email
        document = document or self.document
        return PermissionTuple(
            resource_type="kgm/document",
            resource_id=document_object_id(self.connection.pk, document.pk),
            relation="oauth_viewer",
            subject_type="kgm/user",
            subject_id=user_object_id(self.connection.pk, email),
        )

    def evidence(self, **overrides):
        values = {
            "authorization": self.authorization,
            "source_document": self.document,
            "connection_generation": self.connection.authorization_generation,
            "authorization_generation": self.authorization.authorization_generation,
            "state": UserDocumentVisibility.State.VERIFIED_VISIBLE,
            "checked_at": timezone.now(),
            "spicedb_revision": "zed-token",
            "spicedb_verified_at": timezone.now(),
        }
        values.update(overrides)
        return UserDocumentVisibility.objects.create(**values)

    def test_direct_tuple_and_fresh_matching_evidence_are_both_required(self):
        self.evidence()
        spicedb = DirectTupleSpiceDB({self.direct_tuple()})

        allowed = allowed_source_document_ids(" Reader@Example.com ", spicedb=spicedb)

        self.assertEqual(allowed, (self.document.pk,))
        self.assertEqual(
            spicedb.direct_calls,
            [
                (
                    connection_prefix(self.connection.pk),
                    user_object_id(self.connection.pk, self.email),
                    "",
                )
            ],
        )
        self.assertEqual(spicedb.lookup_calls, [])

    def test_legacy_grant_or_postgresql_evidence_alone_cannot_grant(self):
        self.evidence()
        legacy_resource = document_object_id(self.connection.pk, self.document.pk)
        spicedb = DirectTupleSpiceDB(legacy_resources=(legacy_resource,))

        self.assertEqual(allowed_source_document_ids(self.email, spicedb=spicedb), ())
        self.assertEqual(spicedb.lookup_calls, [])

        UserDocumentVisibility.objects.all().delete()
        spicedb.tuples = {self.direct_tuple()}
        self.assertEqual(allowed_source_document_ids(self.email, spicedb=spicedb), ())

    def test_stale_or_mismatched_visibility_evidence_cannot_grant(self):
        stale = timezone.now() - datetime.timedelta(seconds=1801)
        cases = (
            {"checked_at": stale},
            {"spicedb_verified_at": stale},
            {"spicedb_revision": ""},
            {"state": UserDocumentVisibility.State.DENIED},
            {"connection_generation": uuid.uuid4()},
            {"authorization_generation": uuid.uuid4()},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                UserDocumentVisibility.objects.all().delete()
                self.evidence(**overrides)
                self.assertEqual(
                    allowed_source_document_ids(
                        self.email,
                        spicedb=DirectTupleSpiceDB({self.direct_tuple()}),
                    ),
                    (),
                )

    def test_wrong_user_or_malformed_direct_tuple_cannot_grant(self):
        self.evidence()
        malformed = PermissionTuple(
            resource_type="kgm/document",
            resource_id=document_object_id(self.connection.pk, self.document.pk),
            relation="viewer",
            subject_type="kgm/user",
            subject_id=user_object_id(self.connection.pk, self.email),
        )
        spicedb = DirectTupleSpiceDB({self.direct_tuple(email="other@example.com"), malformed})

        self.assertEqual(allowed_source_document_ids(self.email, spicedb=spicedb), ())

    def test_unavailable_or_ambiguous_authorization_denies_before_spicedb(self):
        cases = (
            {"status": GoogleDriveAuthorization.Status.DISCONNECTED},
            {"granted_scopes": []},
            {"encrypted_refresh_credential": b""},
            {"encryption_key_version": ""},
            {"connection_generation": uuid.uuid4()},
            {"google_issuer": "https://issuer.invalid"},
            {"workspace_domain": "other.example.com"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                original = {field: getattr(self.authorization, field) for field in overrides}
                GoogleDriveAuthorization.objects.filter(pk=self.authorization.pk).update(
                    **overrides
                )
                spicedb = DirectTupleSpiceDB({self.direct_tuple()})
                self.assertEqual(allowed_source_document_ids(self.email, spicedb=spicedb), ())
                self.assertEqual(spicedb.direct_calls, [])
                GoogleDriveAuthorization.objects.filter(pk=self.authorization.pk).update(**original)
                self.authorization.refresh_from_db()

        GoogleDriveAuthorization.objects.create(
            connection=self.connection,
            google_issuer=min(GOOGLE_ISSUERS),
            google_subject="subject-2",
            normalized_email=self.email,
            workspace_domain="example.com",
            encrypted_refresh_credential=b"ciphertext",
            encryption_key_version="v1",
            granted_scopes=sorted(REQUIRED_SCOPES),
            connection_generation=self.connection.authorization_generation,
            status=GoogleDriveAuthorization.Status.ACTIVE,
        )
        spicedb = DirectTupleSpiceDB({self.direct_tuple()})
        self.assertEqual(allowed_source_document_ids(self.email, spicedb=spicedb), ())
        self.assertEqual(spicedb.direct_calls, [])

    @override_settings(GOOGLE_USER_VISIBILITY_MAX_USERS=1)
    def test_configured_user_cap_is_a_retrieval_deny_gate(self):
        GoogleDriveAuthorization.objects.create(
            connection=self.connection,
            google_issuer=min(GOOGLE_ISSUERS),
            google_subject="subject-2",
            normalized_email="other@example.com",
            workspace_domain="example.com",
            encrypted_refresh_credential=b"ciphertext",
            encryption_key_version="v1",
            granted_scopes=sorted(REQUIRED_SCOPES),
            connection_generation=self.connection.authorization_generation,
            status=GoogleDriveAuthorization.Status.ACTIVE,
        )
        self.evidence()
        spicedb = DirectTupleSpiceDB({self.direct_tuple()})

        self.assertEqual(allowed_source_document_ids(self.email, spicedb=spicedb), ())
        self.assertEqual(spicedb.direct_calls, [])

    def test_spicedb_failure_denies_and_logs_no_identity(self):
        self.evidence()
        with self.assertLogs("authorization.lookup", level="WARNING") as captured:
            allowed = allowed_source_document_ids(self.email, spicedb=DirectTupleSpiceDB(fail=True))

        self.assertEqual(allowed, ())
        joined = "\n".join(captured.output)
        self.assertIn("TimeoutError", joined)
        self.assertNotIn(self.email, joined)

    def test_post_retrieval_recheck_uses_only_current_per_user_evidence(self):
        self.evidence()
        self.assertEqual(
            set(fresh_authorized_documents(self.email, {self.document.pk})),
            {self.document.pk},
        )

        UserDocumentVisibility.objects.update(spicedb_verified_at=None)
        self.assertEqual(fresh_authorized_documents(self.email, {self.document.pk}), {})

    def test_delegated_and_per_user_connections_are_never_combined(self):
        DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="legacy-root",
            permission_authority=DriveConnection.PermissionAuthority.DELEGATED_ACL,
        )
        self.evidence()
        spicedb = DirectTupleSpiceDB({self.direct_tuple()})

        self.assertEqual(
            allowed_source_document_ids(self.email, spicedb=spicedb),
            (self.document.pk,),
        )
        self.assertEqual(spicedb.lookup_calls, [])
