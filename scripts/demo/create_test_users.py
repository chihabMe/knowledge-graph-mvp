#!/usr/bin/env python3
"""Create the 4 demo test users via the Admin SDK Directory API.

Uses YOUR OWN Workspace super-admin identity through a one-off, isolated OAuth
credential - never the ingestion service account, and never a standing
domain-wide-delegation grant. This is a one-time provisioning action (you
exercising admin rights you already have, via a script instead of clicking),
not a new capability added to the deployed system.

--- Step 1: get an isolated credential (run this yourself, once) ---

Run in an isolated gcloud config directory so this NEVER touches the main
app's ADC file (~/.config/gcloud/application_default_credentials.json /
/home/user/secrets/application_default_credentials.json), which must keep
impersonating the ingestion service account for the app to keep working:

    CLOUDSDK_CONFIG=/tmp/kg-demo-admin-oauth gcloud auth application-default login \\
      --scopes="openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/admin.directory.user"

When the browser opens, sign in as your Workspace super admin
(e.g. admin@chihab.online) - NOT the personal account used for SA
impersonation.

--- Step 2: run this script ---

    python3 create_test_users.py \\
      --credentials-file /tmp/kg-demo-admin-oauth/application_default_credentials.json \\
      --domain chihab.online

Idempotent: an already-existing user (409 conflict) is reported and skipped,
not treated as a failure.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys

DIRECTORY_SCOPE = "https://www.googleapis.com/auth/admin.directory.user"

DEFAULT_USERS = [
    ("Exec", "Test", "exec-test"),
    ("Eng", "Test", "eng-test"),
    ("Finance", "Test", "finance-test"),
    ("Outsider", "Test", "outsider-test"),
]


def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credentials-file",
        required=True,
        help="Path to the isolated ADC json from Step 1 (NOT the main app's ADC file).",
    )
    parser.add_argument("--domain", default="chihab.online")
    args = parser.parse_args()

    import google.auth.transport.requests
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    # Load explicitly from the given path - deliberately does NOT fall back to
    # google.auth.default(), so this can never accidentally pick up the app's
    # service-account ADC file instead of your isolated admin credential.
    import json

    with open(args.credentials_file) as f:
        info = json.load(f)
    if info.get("type") == "impersonated_service_account":
        print(
            "ERROR: that credentials file impersonates a service account. "
            "This script must run as your own admin identity, not the ingestion "
            "service account. Re-check Step 1 in the module docstring.",
            file=sys.stderr,
        )
        return 1

    # Don't pass an explicit `scopes=` override here: the stored refresh token
    # is already bound to whatever scopes were actually granted during the
    # isolated login (openid + userinfo.email + admin.directory.user), and
    # requesting a narrower scope at refresh time causes Google to reject the
    # refresh with invalid_scope.
    creds = Credentials.from_authorized_user_info(info)
    creds.refresh(google.auth.transport.requests.Request())

    service = build("admin", "directory_v1", credentials=creds)

    print(f"\n=== Creating {len(DEFAULT_USERS)} test users in {args.domain} ===\n")
    created_passwords: dict[str, str] = {}

    for given, family, local_part in DEFAULT_USERS:
        email = f"{local_part}@{args.domain}"
        password = generate_password()
        body = {
            "primaryEmail": email,
            "name": {"givenName": given, "familyName": family},
            "password": password,
            "changePasswordAtNextLogin": True,
        }
        try:
            service.users().insert(body=body).execute()
            created_passwords[email] = password
            print(f"created: {email}")
        except HttpError as exc:
            if exc.resp.status == 409:
                print(f"already exists, skipped: {email}")
            else:
                print(f"FAILED: {email} -> {exc}", file=sys.stderr)

    if created_passwords:
        print("\nTemporary passwords (each must be changed at next login):")
        for email, password in created_passwords.items():
            print(f"  {email}: {password}")
        print(
            "\nSave these somewhere safe now - they are not shown again. You'll "
            "need to log into each account once (separate browser profiles or "
            "incognito windows) to set a real password and complete the Google "
            "OAuth flows for Open WebUI login and Drive visibility consent."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
