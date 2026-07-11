import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import grpc
import grpcutil
from authzed.api.v1 import (
    Client,
    Consistency,
    LookupResourcesRequest,
    ObjectReference,
    ReadRelationshipsRequest,
    ReadSchemaRequest,
    Relationship,
    RelationshipFilter,
    RelationshipUpdate,
    SubjectReference,
    WriteRelationshipsRequest,
    WriteSchemaRequest,
)
from django.conf import settings


@dataclass(frozen=True, order=True)
class PermissionTuple:
    resource_type: str
    resource_id: str
    relation: str
    subject_type: str
    subject_id: str
    subject_relation: str = ""


# Every resource type whose tuples the reconciler owns. Must track
# schema.zed exactly (minus the tuple-less kgm/user): a type missing here
# is never read back, so its stale tuples could not be deleted and the
# post-write verification would pass on a truncated view.
MANAGED_RESOURCE_TYPES = ("kgm/group", "kgm/folder", "kgm/document")


class SpiceDB(Protocol):
    def apply_schema(self, schema: str) -> str: ...

    def read_schema(self) -> str: ...

    def read_managed_tuples(
        self, connection_prefix: str, *, revision: str = ""
    ) -> set[PermissionTuple]: ...

    def write_updates(
        self,
        *,
        touches: Iterable[PermissionTuple],
        deletes: Iterable[PermissionTuple],
    ) -> str: ...

    def lookup_documents(self, user_id: str) -> tuple[str, ...]: ...

    def check(self) -> None: ...


def schema_text() -> str:
    return (Path(__file__).with_name("schema.zed")).read_text(encoding="utf-8")


def canonical_schema(schema: str) -> tuple[tuple[str, str], ...]:
    """Normalize server formatting/order while preserving schema semantics used here."""
    definitions = re.findall(r"definition\s+([^\s{]+)\s*{(.*?)}", schema, re.DOTALL)
    return tuple(sorted((name, " ".join(body.split())) for name, body in definitions))


def _zed_token(response) -> str:
    token = getattr(response, "written_at", None) or getattr(response, "read_at", None)
    return getattr(token, "token", "")


class AuthzedSpiceDB:
    """Small synchronous adapter around the official Authzed v1 client."""

    def __init__(self, client=None, *, timeout=None):
        self._client = client or Client(
            settings.SPICEDB_GRPC_URL,
            grpcutil.insecure_bearer_token_credentials(settings.SPICEDB_GRPC_PRESHARED_KEY),
        )
        self._timeout = timeout if timeout is not None else settings.SPICEDB_REQUEST_TIMEOUT_SECONDS

    def apply_schema(self, schema: str) -> str:
        response = self._client.WriteSchema(
            WriteSchemaRequest(schema=schema), timeout=self._timeout
        )
        return _zed_token(response)

    def read_schema(self) -> str:
        return self._client.ReadSchema(ReadSchemaRequest(), timeout=self._timeout).schema_text

    def read_managed_tuples(
        self, connection_prefix: str, *, revision: str = ""
    ) -> set[PermissionTuple]:
        consistency = (
            Consistency(at_least_as_fresh={"token": revision})
            if revision
            else Consistency(fully_consistent=True)
        )
        tuples: set[PermissionTuple] = set()
        for resource_type in MANAGED_RESOURCE_TYPES:
            responses = self._client.ReadRelationships(
                ReadRelationshipsRequest(
                    consistency=consistency,
                    relationship_filter=RelationshipFilter(
                        resource_type=resource_type,
                        optional_resource_id_prefix=connection_prefix,
                    ),
                ),
                timeout=self._timeout,
            )
            for response in responses:
                item = _from_relationship(response.relationship)
                if item.resource_id.startswith(connection_prefix):
                    tuples.add(item)
        return tuples

    def write_updates(
        self,
        *,
        touches: Iterable[PermissionTuple],
        deletes: Iterable[PermissionTuple],
    ) -> str:
        updates = [
            RelationshipUpdate(
                operation=RelationshipUpdate.OPERATION_TOUCH,
                relationship=_to_relationship(item),
            )
            for item in sorted(set(touches))
        ]
        updates.extend(
            RelationshipUpdate(
                operation=RelationshipUpdate.OPERATION_DELETE,
                relationship=_to_relationship(item),
            )
            for item in sorted(set(deletes))
        )
        revision = ""
        size = settings.SPICEDB_BATCH_SIZE
        for offset in range(0, len(updates), size):
            response = self._client.WriteRelationships(
                WriteRelationshipsRequest(updates=updates[offset : offset + size]),
                timeout=self._timeout,
            )
            revision = _zed_token(response)
        return revision

    def lookup_documents(self, user_id: str) -> tuple[str, ...]:
        responses = self._client.LookupResources(
            LookupResourcesRequest(
                consistency=Consistency(fully_consistent=True),
                resource_object_type="kgm/document",
                permission="view",
                subject=SubjectReference(
                    object=ObjectReference(object_type="kgm/user", object_id=user_id)
                ),
            ),
            timeout=self._timeout,
        )
        return tuple(response.resource_object_id for response in responses)

    def check(self) -> None:
        self._client.ReadSchema(ReadSchemaRequest(), timeout=self._timeout)


def _to_relationship(item: PermissionTuple) -> Relationship:
    return Relationship(
        resource=ObjectReference(object_type=item.resource_type, object_id=item.resource_id),
        relation=item.relation,
        subject=SubjectReference(
            object=ObjectReference(object_type=item.subject_type, object_id=item.subject_id),
            optional_relation=item.subject_relation,
        ),
    )


def _from_relationship(relationship: Relationship) -> PermissionTuple:
    return PermissionTuple(
        resource_type=relationship.resource.object_type,
        resource_id=relationship.resource.object_id,
        relation=relationship.relation,
        subject_type=relationship.subject.object.object_type,
        subject_id=relationship.subject.object.object_id,
        subject_relation=relationship.subject.optional_relation,
    )


SPICEDB_TRANSIENT_ERRORS = (grpc.RpcError, OSError, TimeoutError)
