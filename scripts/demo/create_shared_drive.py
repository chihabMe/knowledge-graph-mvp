#!/usr/bin/env python3
"""Create the demo Shared Drive using the ingestion service account's own credential.

Tries the low-risk path first: the service account that already exists for
content ingestion (knowledge-graph-ingestion@knowledge-graph-pilot.iam.gserviceaccount.com)
attempts to create a Shared Drive directly via the Drive API. No new consent or
credential is needed - this reuses the same impersonated ADC already wired up
for scripts/demo/seed_demo_drive.py.

If Workspace policy blocks service accounts from creating Shared Drives, this
fails with a clear, specific error and the fallback is manual creation (see
private/demo-corpus/README.md Step 1).

Usage:
    python3 create_shared_drive.py --name "Aster Fabrication Co."
"""

from __future__ import annotations

import argparse
import sys
import uuid

DRIVE_WRITE_SCOPE = "https://www.googleapis.com/auth/drive"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Aster Fabrication Co.")
    args = parser.parse_args()

    import google.auth
    import google.auth.transport.requests
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    creds, _ = google.auth.default(scopes=[DRIVE_WRITE_SCOPE])
    creds.refresh(google.auth.transport.requests.Request())
    identity = getattr(creds, "service_account_email", "?")
    print(f"Authenticated as: {identity}")

    service = build("drive", "v3", credentials=creds)

    try:
        result = (
            service.drives()
            .create(requestId=str(uuid.uuid4()), body={"name": args.name})
            .execute()
        )
    except HttpError as exc:
        print(f"\nFAILED to create Shared Drive as {identity}.", file=sys.stderr)
        print(f"Reason: {exc}", file=sys.stderr)
        print(
            "\nThis usually means the Workspace admin policy restricts who can "
            "create Shared Drives (service accounts are commonly excluded). "
            "Fall back to manual creation:\n"
            "  drive.google.com -> Shared drives -> + New -> name it, "
            f"then share it Content Manager with:\n  {identity}\n",
            file=sys.stderr,
        )
        return 1

    drive_id = result["id"]
    print(f"\nCreated Shared Drive {args.name!r}")
    print(f"Shared Drive ID: {drive_id}")
    print(f"URL: https://drive.google.com/drive/folders/{drive_id}")
    print(
        f"\n{identity} is automatically an organizer of this drive - no "
        "separate sharing step needed for it."
    )
    print(
        "\nNext: pass this ID to seed_demo_drive.py as --shared-drive-id, "
        "along with --persona flags for your 4 test users."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
