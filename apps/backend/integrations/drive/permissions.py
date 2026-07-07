import hashlib
import json
from typing import Any

STABLE_PERMISSION_FIELDS = (
    "id",
    "type",
    "role",
    "emailAddress",
    "domain",
    "allowFileDiscovery",
    "deleted",
    "pendingOwner",
    "inherited",
)


def source_permissions_version(permissions: list[dict[str, Any]] | None) -> str:
    permissions = permissions or []
    canonical_permissions = [
        {field: permission.get(field) for field in STABLE_PERMISSION_FIELDS if field in permission}
        for permission in permissions
    ]
    canonical_permissions.sort(
        key=lambda permission: (
            str(permission.get("id", "")),
            str(permission.get("type", "")),
            str(permission.get("emailAddress", "")),
            str(permission.get("domain", "")),
            str(permission.get("role", "")),
        )
    )
    payload = json.dumps(canonical_permissions, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def has_public_link(permissions: list[dict[str, Any]] | None) -> bool:
    permissions = permissions or []
    return any(permission.get("type") == "anyone" for permission in permissions)


def has_domain_visibility(permissions: list[dict[str, Any]] | None) -> bool:
    permissions = permissions or []
    return any(permission.get("type") == "domain" for permission in permissions)
