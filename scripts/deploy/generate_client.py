#!/usr/bin/env python3
"""Generate a per-client deployment config for the knowledge-graph stack.

Produces `clients/<slug>/`:
  - <slug>.env  : a full env derived from .env.example, with every SECRET
                  freshly generated (and DATABASE_URL/NEO4J_AUTH updated to
                  match) and production flags set.
  - secrets/google-user-token-keyring.json : a valid versioned Fernet keyring,
                  guaranteed distinct from every other secret (the app also
                  enforces this at boot).

Human-supplied values (Google OAuth client, OpenRouter key, the client's
Workspace domain) are written as __FILL_ME__ so the operator can see exactly
what remains. With --domain, the host/URL/CSRF/Django-OAuth-redirect values are
filled automatically.

Usage:
    python scripts/deploy/generate_client.py <slug> [--domain BASE_DOMAIN]
    # e.g. python scripts/deploy/generate_client.py acme --domain chihab.online

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

# Django-owned OAuth callback paths (from config/settings_validators.py).
DRIVE_CALLBACK = "/api/drive/oauth/callback"
SESSION_CALLBACK = "/api/session/google/callback"


def token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def fernet_key() -> str:
    # 32 random bytes, urlsafe-base64 — exactly what the keyring validator wants.
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def build_values(slug: str, domain: str | None, keyring_path: Path) -> tuple[dict, list[str]]:
    pg = token(18)
    neo = token(18)
    values: dict[str, str] = {
        # --- generated secrets ---
        "DJANGO_SECRET_KEY": token(50),
        "POSTGRES_PASSWORD": pg,
        "DATABASE_URL": f"postgres://kg_user:{pg}@postgres:5432/knowledge_graph",
        "NEO4J_PASSWORD": neo,
        "NEO4J_AUTH": f"neo4j/{neo}",
        "SPICEDB_GRPC_PRESHARED_KEY": token(24),
        "WEBUI_SECRET_KEY": token(24),
        "OPEN_WEBUI_BACKEND_API_KEY": token(24),
        "OPEN_WEBUI_IDENTITY_JWT_SECRET": token(32),
        "GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE": str(keyring_path),
        # --- production flags ---
        "DJANGO_DEBUG": "false",
        "OPEN_WEBUI_COMPATIBLE_API_ENABLED": "true",
        "GOOGLE_SESSION_OAUTH_ENABLED": "true",
        # --- human-supplied (external) ---
        "GOOGLE_CLIENT_ID": "__FILL_ME__",
        "GOOGLE_CLIENT_SECRET": "__FILL_ME__",
        "OPENROUTER_API_KEY": "__FILL_ME__",
        "GOOGLE_USER_OAUTH_ALLOWED_DOMAIN": "__FILL_ME__",
    }

    todos = [
        "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET  (Google Cloud OAuth client)",
        "OPENROUTER_API_KEY  (OpenRouter account)",
        "GOOGLE_USER_OAUTH_ALLOWED_DOMAIN  (the client's Workspace domain)",
        "GOOGLE_REDIRECT_URI  (Open WebUI's Google login callback — verify path in Google console)",
        "Drop the Google OAuth client + service-account JSON files into "
        f"clients/{slug}/secrets/ and point their *_FILE vars at them",
    ]

    if domain:
        webui = f"{slug}.{domain}"
        api = f"api.{slug}.{domain}"
        values.update(
            {
                "OPEN_WEBUI_HOST": webui,
                "DJANGO_HOST": api,
                "WEBUI_URL": f"https://{webui}",
                "DJANGO_ALLOWED_HOSTS": api,
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
    return values, todos


def render_env(values: dict) -> str:
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
    args = ap.parse_args()

    if not SLUG_RE.fullmatch(args.slug):
        sys.exit(f"invalid slug '{args.slug}': use lowercase letters, digits, and hyphens (2-32 chars)")
    if not ENV_EXAMPLE.exists():
        sys.exit(f"missing template: {ENV_EXAMPLE}")

    client_dir = CLIENTS / args.slug
    if client_dir.exists():
        sys.exit(f"refusing to overwrite existing {client_dir} (delete it first if you mean to)")
    secrets_dir = client_dir / "secrets"
    secrets_dir.mkdir(parents=True)

    keyring_path = secrets_dir / "google-user-token-keyring.json"
    keyring = {"active_version": "v1", "keys": {"v1": fernet_key()}}
    keyring_path.write_text(json.dumps(keyring, indent=2) + "\n", encoding="utf-8")
    keyring_path.chmod(0o600)

    values, todos = build_values(args.slug, args.domain, keyring_path)
    env_path = client_dir / f"{args.slug}.env"
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
