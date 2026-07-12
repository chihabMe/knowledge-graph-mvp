from dataclasses import dataclass

from integrations.drive.credentials import (
    ServiceAccountKeyError,
    load_service_account_credentials,
)
from integrations.models import DriveConnection

DIRECTORY_GROUP_MEMBER_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly"
)


class GroupResolutionError(RuntimeError):
    """A referenced group could not be resolved completely."""


@dataclass(frozen=True)
class GroupMembership:
    users: frozenset[str]
    child_groups: frozenset[str]


def build_directory_service(connection: DriveConnection):
    if not connection.delegated_subject_email:
        raise GroupResolutionError("Delegated subject required for Directory API access.")
    try:
        credentials = load_service_account_credentials(
            [DIRECTORY_GROUP_MEMBER_SCOPE], subject=connection.delegated_subject_email
        )
    except ServiceAccountKeyError as exc:
        # One controlled message on purpose: group resolution feeds run
        # error codes, so key-path detail must not leak through this path.
        raise GroupResolutionError("Directory credentials unavailable.") from exc

    from googleapiclient.discovery import build

    try:
        return build("admin", "directory_v1", credentials=credentials, cache_discovery=False)
    except (OSError, ValueError) as exc:
        raise GroupResolutionError("Directory credentials unavailable.") from exc


class GoogleGroupResolver:
    def __init__(self, service=None):
        self._service = service

    def resolve(
        self, connection: DriveConnection, group_emails: set[str]
    ) -> dict[str, GroupMembership]:
        service = self._service or build_directory_service(connection)
        resolved: dict[str, GroupMembership] = {}
        visiting: set[str] = set()

        def visit(group_email: str) -> None:
            group_email = group_email.strip().lower()
            if group_email in resolved:
                return
            if group_email in visiting:
                raise GroupResolutionError("Nested group cycle detected.")
            visiting.add(group_email)
            users: set[str] = set()
            children: set[str] = set()
            page_token = None
            try:
                while True:
                    response = (
                        service.members()
                        .list(groupKey=group_email, pageToken=page_token, maxResults=200)
                        .execute()
                    )
                    for member in response.get("members", []):
                        email = str(member.get("email", "")).strip().lower()
                        if not email or member.get("status", "ACTIVE") != "ACTIVE":
                            continue
                        if member.get("type") == "GROUP":
                            children.add(email)
                        elif member.get("type") == "USER":
                            users.add(email)
                    page_token = response.get("nextPageToken")
                    if not page_token:
                        break
            except Exception as exc:
                raise GroupResolutionError("Group membership lookup failed.") from exc
            for child in sorted(children):
                visit(child)
            visiting.remove(group_email)
            resolved[group_email] = GroupMembership(frozenset(users), frozenset(children))

        for email in sorted(group_emails):
            visit(email)
        return resolved
