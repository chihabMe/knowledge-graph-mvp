#!/usr/bin/env python3
"""Generate a per-client deployment config for the knowledge-graph stack.

Produces `clients/<slug>/`:
  - <slug>.env  : a full env derived from .env.example, with every SECRET
                  freshly generated (and DATABASE_URL/NEO4J_AUTH updated to
                  match) and production flags set.
  - secrets/google-user-token-keyring.json : a valid versioned Fernet keyring,
                  guaranteed distinct from every other secret (the app also
                  enforces this at boot).

Human-supplied values (the two shared Google OAuth clients, OpenRouter key,
the client's Workspace domain, and its dedicated ingestion service account)
are written as __FILL_ME__ unless supplied. With --domain, the public
host/URL/callback values are filled automatically.

Usage:
    python scripts/deploy/generate_client.py <slug> --domain BASE_DOMAIN \
      --workspace-domain CLIENT_DOMAIN \
      --service-account-email SERVICE_ACCOUNT_EMAIL
    python scripts/deploy/generate_client.py <slug> --check --target coolify

Never commit clients/<slug>/ — see clients/.gitignore.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO / ".env.example"
CLIENTS = REPO / "clients"

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
SERVICE_ACCOUNT_RE = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@"
    r"[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)

# Django-owned OAuth callback paths (from config/settings_validators.py).
DRIVE_CALLBACK = "/api/drive/oauth/callback"
SESSION_CALLBACK = "/api/session/google/callback"


def token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def fernet_key() -> str:
    # 32 random bytes, urlsafe-base64 — exactly what the keyring validator wants.
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def build_values(
    slug: str,
    domain: str | None,
    keyring_path: Path,
    *,
    workspace_domain: str | None = None,
    service_account_email: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    pg = token(18)
    neo = token(18)
    workspace = workspace_domain or "__FILL_ME__"
    ingestion_identity = service_account_email or "__FILL_ME__"
    google_cloud_project = "__FILL_ME__"
    if service_account_email:
        google_cloud_project = service_account_email.split("@", 1)[1].removesuffix(
            ".iam.gserviceaccount.com"
        )
    oauth_secret_path = keyring_path.with_name("google-user-oauth-client.json")
    keyring_payload = json.loads(keyring_path.read_text(encoding="utf-8"))
    keyring_json = json.dumps(keyring_payload, separators=(",", ":"))
    values: dict[str, str] = {
        # --- generated secrets ---
        "DJANGO_SECRET_KEY": token(50),
        "POSTGRES_PASSWORD": pg,
        "DATABASE_URL": f"postgres://kg_user:{pg}@postgres:5432/knowledge_graph",
        "NEO4J_PASSWORD": neo,
        "NEO4J_AUTH": f"neo4j/{neo}",
        "KG_NEO4J_PASSWORD": neo,
        "SPICEDB_GRPC_PRESHARED_KEY": token(24),
        "WEBUI_SECRET_KEY": token(24),
        "OPEN_WEBUI_BACKEND_API_KEY": token(24),
        "OPEN_WEBUI_IDENTITY_JWT_SECRET": token(32),
        "GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE": str(keyring_path),
        # --- production flags ---
        "DJANGO_DEBUG": "false",
        "OPEN_WEBUI_COMPATIBLE_API_ENABLED": "true",
        "GOOGLE_SESSION_OAUTH_ENABLED": "true",
        "GOOGLE_DRIVE_AUTH_MODE": "application_default",
        "GOOGLE_ADC_FILE": "",
        "GOOGLE_ADC_CONTAINER_FILE": "",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "",
        "GOOGLE_DRIVE_ROOT_ID": "",
        "GOOGLE_SHARED_DRIVE_ID": "",
        # --- human-supplied (external) ---
        "GOOGLE_CLIENT_ID": "__FILL_ME__",
        "GOOGLE_CLIENT_SECRET": "__FILL_ME__",
        "GOOGLE_USER_OAUTH_CLIENT_ID": "__FILL_ME__",
        "GOOGLE_USER_OAUTH_CLIENT_SECRET_FILE": str(oauth_secret_path),
        "KG_GOOGLE_USER_OAUTH_CLIENT_JSON": "__FILL_ME__",
        "KG_GOOGLE_USER_TOKEN_KEYRING_JSON": keyring_json,
        "OPENROUTER_API_KEY": "__FILL_ME__",
        "GOOGLE_WORKSPACE_DOMAIN": workspace,
        "GOOGLE_USER_OAUTH_ALLOWED_DOMAIN": workspace,
        "GOOGLE_INGESTION_SERVICE_ACCOUNT_EMAIL": ingestion_identity,
        "GOOGLE_CLOUD_PROJECT": google_cloud_project,
    }

    todos = [
        "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET  (shared login OAuth client)",
        "GOOGLE_USER_OAUTH_CLIENT_ID and OAuth client JSON  (shared Drive OAuth client)",
        "OPENROUTER_API_KEY  (OpenRouter account)",
    ]
    if not workspace_domain:
        todos.append("GOOGLE_WORKSPACE_DOMAIN / GOOGLE_USER_OAUTH_ALLOWED_DOMAIN")
    if not service_account_email:
        todos.append("GOOGLE_INGESTION_SERVICE_ACCOUNT_EMAIL  (dedicated per-client SA)")

    if domain:
        webui = f"{slug}.{domain}"
        api = f"api.{slug}.{domain}"
        values.update(
            {
                "OPEN_WEBUI_HOST": webui,
                "DJANGO_HOST": api,
                "WEBUI_URL": f"https://{webui}",
                "OPENAI_API_BASE_URL": f"https://{api}/v1",
                "GOOGLE_REDIRECT_URI": f"https://{webui}/oauth/google/callback",
                "DJANGO_ALLOWED_HOSTS": f"{api},django,kg-django",
                "DJANGO_CSRF_TRUSTED_ORIGINS": f"https://{api},https://{webui}",
                "GOOGLE_SESSION_OAUTH_REDIRECT_URI": f"https://{api}{SESSION_CALLBACK}",
                "GOOGLE_USER_OAUTH_REDIRECT_URI": f"https://{api}{DRIVE_CALLBACK}",
            }
        )
    else:
        todos.insert(
            0,
            "domains: DJANGO_HOST / OPEN_WEBUI_HOST / WEBUI_URL / CSRF / Django "
            "OAuth redirect URIs  (or re-run with --domain BASE_DOMAIN)",
        )
        todos.insert(
            1,
            "GOOGLE_REDIRECT_URI  (Open WebUI's Google login callback — "
            "verify path in Google console)",
        )
    return values, todos


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def _unresolved(value: str) -> bool:
    lowered = value.lower()
    return (
        not value
        or "__fill_me__" in lowered
        or lowered.startswith(("change-this", "replace-me", "django-insecure-", "unsafe-"))
    )


def _json_object(value: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def validate_client_values(
    values: dict[str, str],
    *,
    target: str,
) -> list[str]:
    """Return safe configuration errors without echoing credential values."""
    problems: list[str] = []
    required = (
        "DJANGO_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "KG_NEO4J_PASSWORD",
        "SPICEDB_GRPC_PRESHARED_KEY",
        "WEBUI_SECRET_KEY",
        "OPEN_WEBUI_BACKEND_API_KEY",
        "OPEN_WEBUI_IDENTITY_JWT_SECRET",
        "OPENROUTER_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_USER_OAUTH_CLIENT_ID",
        "GOOGLE_WORKSPACE_DOMAIN",
        "GOOGLE_USER_OAUTH_ALLOWED_DOMAIN",
        "GOOGLE_INGESTION_SERVICE_ACCOUNT_EMAIL",
        "OPEN_WEBUI_HOST",
        "DJANGO_HOST",
        "WEBUI_URL",
        "OPENAI_API_BASE_URL",
        "GOOGLE_REDIRECT_URI",
        "GOOGLE_SESSION_OAUTH_REDIRECT_URI",
        "GOOGLE_USER_OAUTH_REDIRECT_URI",
    )
    for key in required:
        if _unresolved(values.get(key, "")):
            problems.append(f"{key} is missing or still a placeholder")

    workspace = values.get("GOOGLE_WORKSPACE_DOMAIN", "")
    if workspace and not DOMAIN_RE.fullmatch(workspace):
        problems.append("GOOGLE_WORKSPACE_DOMAIN is not a valid lowercase domain")
    if values.get("GOOGLE_USER_OAUTH_ALLOWED_DOMAIN") != workspace:
        problems.append("GOOGLE_USER_OAUTH_ALLOWED_DOMAIN must match GOOGLE_WORKSPACE_DOMAIN")

    service_account_email = values.get("GOOGLE_INGESTION_SERVICE_ACCOUNT_EMAIL", "")
    if service_account_email and not SERVICE_ACCOUNT_RE.fullmatch(service_account_email):
        problems.append(
            "GOOGLE_INGESTION_SERVICE_ACCOUNT_EMAIL must be a dedicated "
            "*.iam.gserviceaccount.com identity"
        )
    if values.get("GOOGLE_DRIVE_AUTH_MODE") != "application_default":
        problems.append(
            "GOOGLE_DRIVE_AUTH_MODE must be application_default for the keyless GCE POC"
        )
    if values.get("GOOGLE_SERVICE_ACCOUNT_FILE"):
        problems.append("GOOGLE_SERVICE_ACCOUNT_FILE must stay empty for keyless GCE ADC")

    api_host = values.get("DJANGO_HOST", "")
    webui_host = values.get("OPEN_WEBUI_HOST", "")
    expected_urls = {
        "WEBUI_URL": f"https://{webui_host}",
        "OPENAI_API_BASE_URL": f"https://{api_host}/v1",
        "GOOGLE_REDIRECT_URI": f"https://{webui_host}/oauth/google/callback",
        "GOOGLE_SESSION_OAUTH_REDIRECT_URI": f"https://{api_host}{SESSION_CALLBACK}",
        "GOOGLE_USER_OAUTH_REDIRECT_URI": f"https://{api_host}{DRIVE_CALLBACK}",
    }
    for key, expected in expected_urls.items():
        if values.get(key) != expected:
            problems.append(f"{key} does not match the generated public host")

    root_id = values.get("GOOGLE_DRIVE_ROOT_ID", "")
    if root_id.lower() in {"replace-me", "change-this", "__fill_me__"}:
        problems.append("GOOGLE_DRIVE_ROOT_ID must be empty until selected, or a real Drive ID")

    user_client_id = values.get("GOOGLE_USER_OAUTH_CLIENT_ID", "")
    if user_client_id and not user_client_id.endswith(".apps.googleusercontent.com"):
        problems.append("GOOGLE_USER_OAUTH_CLIENT_ID is not a Google OAuth client ID")

    if target == "coolify":
        oauth_payload = _json_object(values.get("KG_GOOGLE_USER_OAUTH_CLIENT_JSON", ""))
        oauth_web = oauth_payload.get("web") if oauth_payload else None
        if not isinstance(oauth_web, dict):
            problems.append(
                "KG_GOOGLE_USER_OAUTH_CLIENT_JSON must contain the shared web OAuth client JSON"
            )
        else:
            if oauth_web.get("client_id") != user_client_id:
                problems.append("Drive OAuth JSON client_id must match GOOGLE_USER_OAUTH_CLIENT_ID")
            if _unresolved(str(oauth_web.get("client_secret", ""))):
                problems.append("Drive OAuth JSON client_secret is missing")
        if _json_object(values.get("KG_GOOGLE_USER_TOKEN_KEYRING_JSON", "")) is None:
            problems.append(
                "KG_GOOGLE_USER_TOKEN_KEYRING_JSON must contain the generated keyring JSON"
            )
    else:
        for key in (
            "GOOGLE_USER_OAUTH_CLIENT_SECRET_FILE",
            "GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE",
        ):
            path_value = values.get(key, "")
            if not path_value or not Path(path_value).is_file():
                problems.append(f"{key} must name a readable local file")

    return problems


def render_env(values: dict[str, str]) -> str:
    """Rewrite .env.example line-by-line, replacing values for known keys."""
    out: list[str] = []
    seen: set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if m and m.group(1) in values:
            key = m.group(1)
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    # Append any keys not present in the template (e.g. new flags).
    extra = [k for k in values if k not in seen]
    if extra:
        out.append("")
        out.append("# --- added by generate_client.py (not in .env.example) ---")
        out.extend(f"{k}={values[k]}" for k in extra)
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a per-client deployment config.")
    ap.add_argument("slug", help="client slug: lowercase letters, digits, hyphens")
    ap.add_argument("--domain", help="base domain, e.g. chihab.online")
    ap.add_argument("--workspace-domain", help="client Google Workspace domain")
    ap.add_argument("--service-account-email", help="dedicated per-client ingestion identity")
    ap.add_argument("--check", action="store_true", help="validate an existing client env")
    ap.add_argument("--target", choices=("coolify", "local"), default="coolify")
    args = ap.parse_args()

    if not SLUG_RE.fullmatch(args.slug):
        sys.exit(
            f"invalid slug '{args.slug}': use lowercase letters, digits, and hyphens (2-32 chars)"
        )
    if not ENV_EXAMPLE.exists():
        sys.exit(f"missing template: {ENV_EXAMPLE}")

    client_dir = CLIENTS / args.slug
    env_path = client_dir / f"{args.slug}.env"
    if args.check:
        if not env_path.is_file():
            sys.exit(f"missing {env_path}; generate the client config first")
        problems = validate_client_values(parse_env(env_path), target=args.target)
        if problems:
            print(f"Preflight failed for {args.slug} ({args.target}):", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            sys.exit(1)
        print(f"Preflight passed for {args.slug} ({args.target}).")
        return

    for label, value in (
        ("domain", args.domain),
        ("workspace domain", args.workspace_domain),
    ):
        if value and not DOMAIN_RE.fullmatch(value):
            sys.exit(f"invalid {label}: use a lowercase DNS domain")
    if args.service_account_email and not SERVICE_ACCOUNT_RE.fullmatch(args.service_account_email):
        sys.exit(
            "invalid service account email: use a dedicated *.iam.gserviceaccount.com identity"
        )
    if client_dir.exists():
        sys.exit(f"refusing to overwrite existing {client_dir} (delete it first if you mean to)")
    secrets_dir = client_dir / "secrets"
    secrets_dir.mkdir(parents=True)
    secrets_dir.chmod(0o700)

    keyring_path = secrets_dir / "google-user-token-keyring.json"
    keyring = {"active_version": "v1", "keys": {"v1": fernet_key()}}
    keyring_path.write_text(json.dumps(keyring, indent=2) + "\n", encoding="utf-8")
    keyring_path.chmod(0o600)

    values, todos = build_values(
        args.slug,
        args.domain,
        keyring_path,
        workspace_domain=args.workspace_domain,
        service_account_email=args.service_account_email,
    )
    env_path.write_text(render_env(values), encoding="utf-8")
    env_path.chmod(0o600)

    print(f"Generated {client_dir}/")
    print(f"  env:     {env_path}")
    print(f"  keyring: {keyring_path}  (fresh Fernet key, distinct from all secrets)")
    print("  generated secrets: DJANGO_SECRET_KEY, POSTGRES_PASSWORD, NEO4J_PASSWORD,")
    print("    SPICEDB_GRPC_PRESHARED_KEY, WEBUI_SECRET_KEY, OPEN_WEBUI_BACKEND_API_KEY,")
    print("    OPEN_WEBUI_IDENTITY_JWT_SECRET")
    print("\n  STILL TO FILL (human/external):")
    for t in todos:
        print(f"    - {t}")


if __name__ == "__main__":
    main()
