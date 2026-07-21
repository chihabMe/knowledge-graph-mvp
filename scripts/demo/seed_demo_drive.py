#!/usr/bin/env python3
"""Seed the client-facing demo Drive corpus for the permission-safe retrieval proof.

SUPERSEDED (2026-07-21): the live Milestone 2 proof used a smaller,
connector-built, 2-employee/Google-Docs-only scenario instead of running this
script - see docs/runbooks/demo-drive-permission-proof.md. This script (and
private/demo-corpus/manifest.yaml) are kept for reference/future expansion
but were not exercised for that evidence.

This is operator tooling, not part of the Django app. It reads
`private/demo-corpus/manifest.yaml` and creates the folder tree, Docs/Sheets/PDFs,
and per-folder sharing inside a Shared Drive you already created and shared with
the ingestion service account as Content Manager.

It deliberately requests full Drive write scope (`drive`), separate from the
read-only scope the ingestion pipeline uses in production
(`integrations/drive/google_client.py` uses `drive.readonly`). This script is a
one-off content-authoring tool, never imported by the Django app, and never
runs inside the app containers.

Prerequisites (human steps, not done by this script):
  1. Create a Shared Drive (e.g. "Aster Fabrication Co.") in the Workspace domain.
  2. Share it with the ingestion service account as Content Manager:
       knowledge-graph-ingestion@knowledge-graph-pilot.iam.gserviceaccount.com
  3. Create the pilot Workspace test user accounts you intend to use.

Usage:
    cd scripts/demo
    python3 seed_demo_drive.py \\
        --shared-drive-id <id> \\
        --persona exec=exec-test@chihab.online \\
        --persona engineering=eng-test@chihab.online \\
        --persona finance=finance-test@chihab.online \\
        --dry-run

Drop --dry-run to actually call the Drive API. Re-running is idempotent: the
script looks up existing folders/files by name+parent before creating.
"""

from __future__ import annotations

import argparse
import io
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "private" / "demo-corpus"
MANIFEST_PATH = CORPUS_DIR / "manifest.yaml"

DRIVE_WRITE_SCOPE = "https://www.googleapis.com/auth/drive"

FOLDER_MIME = "application/vnd.google-apps.folder"
GDOC_MIME = "application/vnd.google-apps.document"
GSHEET_MIME = "application/vnd.google-apps.spreadsheet"


# ---------------------------------------------------------------------------
# Minimal dependency-free PDF writer (no reportlab/pandoc/libreoffice available
# in the seeding environment). Produces a valid, simple single-or-multi-page
# PDF with left-aligned monospace text. Good enough for ingestion testing -
# this is demo content, not a real client deliverable.
# ---------------------------------------------------------------------------
_PDF_ASCII_NORMALIZE = {
    "\u2014": "-",  # em dash
    "\u2013": "-",  # en dash
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
}


def _normalize_for_pdf(text: str) -> str:
    for src, dst in _PDF_ASCII_NORMALIZE.items():
        text = text.replace(src, dst)
    return text


def _pdf_escape(text: str) -> str:
    text = _normalize_for_pdf(text)
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_simple_pdf(title: str, body: str) -> bytes:
    lines: list[str] = []
    for raw_line in body.splitlines() or [""]:
        while len(raw_line) > 95:
            lines.append(raw_line[:95])
            raw_line = raw_line[95:]
        lines.append(raw_line)

    lines_per_page = 55
    pages: list[list[str]] = [
        lines[i : i + lines_per_page] for i in range(0, len(lines), lines_per_page)
    ] or [[]]

    objects: list[bytes] = []

    def add_object(body_bytes: bytes) -> int:
        objects.append(body_bytes)
        return len(objects)

    catalog_num = add_object(b"")  # placeholder, filled after we know pages_num
    pages_kids_nums: list[int] = []
    font_num = add_object(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"
    )

    page_content_nums = []
    for page_lines in pages:
        # Title line, then body lines below it at a smaller size.
        stream_parts = [
            "BT",
            "/F1 11 Tf",
            f"50 770 Td ({_pdf_escape(title)}) Tj",
            "ET",
        ]
        body_stream = ["BT", "/F1 9 Tf", "50 745 Td 13 TL"]
        for line in page_lines:
            body_stream.append(f"({_pdf_escape(line)}) Tj T*")
        body_stream.append("ET")
        content = "\n".join(stream_parts + body_stream).encode("latin-1", "replace")
        compressed = zlib.compress(content)
        stream_obj = (
            b"<< /Length "
            + str(len(compressed)).encode()
            + b" /Filter /FlateDecode >>\nstream\n"
            + compressed
            + b"\nendstream"
        )
        page_content_nums.append(add_object(stream_obj))

    for content_num in page_content_nums:
        page_num = add_object(b"")  # placeholder
        pages_kids_nums.append(page_num)

    pages_num = add_object(b"")  # placeholder for /Pages
    root_num = catalog_num

    # Now fill in placeholders with correct cross-references.
    kids_refs = " ".join(f"{n} 0 R" for n in pages_kids_nums)
    objects[pages_num - 1] = (
        b"<< /Type /Pages /Kids [" + kids_refs.encode() + b"] /Count "
        + str(len(pages_kids_nums)).encode()
        + b" >>"
    )
    for page_num, content_num in zip(pages_kids_nums, page_content_nums):
        objects[page_num - 1] = (
            b"<< /Type /Page /Parent "
            + str(pages_num).encode()
            + b" 0 R /Resources << /Font << /F1 "
            + str(font_num).encode()
            + b" 0 R >> >> /MediaBox [0 0 612 792] /Contents "
            + str(content_num).encode()
            + b" 0 R >>"
        )
    objects[root_num - 1] = (
        b"<< /Type /Catalog /Pages " + str(pages_num).encode() + b" 0 R >>"
    )

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode())
        buf.write(obj)
        buf.write(b"\nendobj\n")
    xref_offset = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode())
    buf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root {root_num} 0 R >>\n".encode()
    )
    buf.write(f"startxref\n{xref_offset}\n%%EOF".encode())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Manifest model
# ---------------------------------------------------------------------------
@dataclass
class FolderSpec:
    path: str
    share_with: list[str]


@dataclass
class DocSpec:
    id: str
    folder: str
    title: str
    target_mime: str  # gdoc | gsheet | pdf
    content_file: str
    entities: list[str] = field(default_factory=list)


@dataclass
class ExclusionSpec:
    id: str
    folder: str
    title: str
    kind: str  # unsupported_binary | public_link_pdf
    content_file: Optional[str] = None
    note: str = ""


def load_manifest() -> tuple[str, list[FolderSpec], list[DocSpec], list[ExclusionSpec]]:
    data = yaml.safe_load(MANIFEST_PATH.read_text())
    root_name = data["drive"]["root_name"]
    folders = [FolderSpec(f["path"], f["share_with"]) for f in data["folders"]]
    docs = [
        DocSpec(
            id=d["id"],
            folder=d["folder"],
            title=d["title"],
            target_mime=d["target_mime"],
            content_file=d["content_file"],
            entities=d.get("entities", []),
        )
        for d in data["documents"]
    ]
    exclusions = [
        ExclusionSpec(
            id=e["id"],
            folder=e["folder"],
            title=e["title"],
            kind=e["kind"],
            content_file=e.get("content_file"),
            note=e.get("note", ""),
        )
        for e in data.get("exclusion_test_files", [])
    ]
    return root_name, folders, docs, exclusions


# ---------------------------------------------------------------------------
# Drive operations (real API calls happen only here; everything above is pure)
# ---------------------------------------------------------------------------
class DriveSeeder:
    def __init__(self, service, shared_drive_id: str, personas: dict[str, str], dry_run: bool):
        self.service = service
        self.shared_drive_id = shared_drive_id
        self.personas = personas
        self.dry_run = dry_run
        self._folder_ids: dict[str, str] = {"": shared_drive_id}

    def _find_child(self, name: str, parent_id: str, mime: Optional[str] = None) -> Optional[str]:
        if self.dry_run:
            return None
        query = f"name = {name!r} and {parent_id!r} in parents and trashed = false"
        if mime:
            query += f" and mimeType = {mime!r}"
        resp = (
            self.service.files()
            .list(
                q=query,
                driveId=self.shared_drive_id,
                corpora="drive",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id,name)",
            )
            .execute()
        )
        files = resp.get("files", [])
        return files[0]["id"] if files else None

    def ensure_folder(self, path: str) -> str:
        if path in self._folder_ids:
            return self._folder_ids[path]
        parent_path = "/".join(path.split("/")[:-1])
        name = path.split("/")[-1]
        parent_id = self.ensure_folder(parent_path) if parent_path else self.shared_drive_id

        if self.dry_run:
            print(f"[dry-run] would create folder: {path!r} under parent {parent_path or '<root>'!r}")
            self._folder_ids[path] = f"dryrun-folder-{path}"
            return self._folder_ids[path]

        existing = self._find_child(name, parent_id, FOLDER_MIME)
        if existing:
            print(f"folder exists, reusing: {path}")
            self._folder_ids[path] = existing
            return existing

        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        created = (
            self.service.files()
            .create(body=body, supportsAllDrives=True, fields="id")
            .execute()
        )
        folder_id = created["id"]
        print(f"created folder: {path} -> {folder_id}")
        self._folder_ids[path] = folder_id
        return folder_id

    def share_folder(self, folder_id: str, persona_keys: list[str]) -> None:
        emails = sorted({self.personas[k] for k in persona_keys if k in self.personas})
        missing = sorted({k for k in persona_keys if k not in self.personas})
        if missing:
            print(f"  (skipping unknown personas, not passed via --persona: {missing})")
        for email in emails:
            if self.dry_run:
                print(f"  [dry-run] would share with {email} (role=reader)")
                continue
            self.service.permissions().create(
                fileId=folder_id,
                supportsAllDrives=True,
                sendNotificationEmail=False,
                body={"type": "user", "role": "reader", "emailAddress": email},
            ).execute()
            print(f"  shared with {email}")

    def create_from_text(self, folder_id: str, title: str, target_mime: str, text: str) -> Optional[str]:
        if self.dry_run:
            print(f"[dry-run] would create {target_mime} {title!r} ({len(text)} chars)")
            return None

        from googleapiclient.http import MediaIoBaseUpload

        existing = self._find_child(title, folder_id)
        if existing:
            print(f"file exists, skipping: {title}")
            return existing

        if target_mime == "pdf":
            payload = make_simple_pdf(title, text)
            media = MediaIoBaseUpload(io.BytesIO(payload), mimetype="application/pdf")
            body = {"name": f"{title}.pdf", "parents": [folder_id], "mimeType": "application/pdf"}
        elif target_mime == "gdoc":
            media = MediaIoBaseUpload(io.BytesIO(text.encode("utf-8")), mimetype="text/plain")
            body = {"name": title, "parents": [folder_id], "mimeType": GDOC_MIME}
        elif target_mime == "gsheet":
            media = MediaIoBaseUpload(io.BytesIO(text.encode("utf-8")), mimetype="text/csv")
            body = {"name": title, "parents": [folder_id], "mimeType": GSHEET_MIME}
        else:
            raise ValueError(f"unknown target_mime: {target_mime}")

        created = (
            self.service.files()
            .create(body=body, media_body=media, supportsAllDrives=True, fields="id")
            .execute()
        )
        print(f"created {target_mime}: {title} -> {created['id']}")
        return created["id"]

    def create_binary_placeholder(self, folder_id: str, title: str, mime: str, payload: bytes) -> Optional[str]:
        if self.dry_run:
            print(f"[dry-run] would create placeholder binary {title!r} ({mime})")
            return None
        from googleapiclient.http import MediaIoBaseUpload

        existing = self._find_child(title, folder_id)
        if existing:
            print(f"file exists, skipping: {title}")
            return existing
        media = MediaIoBaseUpload(io.BytesIO(payload), mimetype=mime)
        body = {"name": title, "parents": [folder_id], "mimeType": mime}
        created = (
            self.service.files()
            .create(body=body, media_body=media, supportsAllDrives=True, fields="id")
            .execute()
        )
        print(f"created placeholder: {title} -> {created['id']}")
        return created["id"]

    def make_public_link(self, file_id: Optional[str]) -> None:
        if self.dry_run or file_id is None:
            print("  [dry-run] would share as 'anyone with the link'")
            return
        self.service.permissions().create(
            fileId=file_id,
            supportsAllDrives=True,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        print("  shared as 'anyone with the link'")


def parse_personas(pairs: list[str]) -> dict[str, str]:
    personas: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--persona must be key=email, got: {pair!r}")
        key, email = pair.split("=", 1)
        personas[key.strip()] = email.strip()
    return personas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-drive-id", required=True, help="Shared Drive ID (from its URL)")
    parser.add_argument(
        "--persona",
        action="append",
        default=[],
        metavar="KEY=EMAIL",
        help="Map a manifest persona key (exec, engineering, finance, operations, sales, hr, all) "
        "to a real test-user email. Repeatable. 'all' maps to every persona you pass.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions, call no APIs")
    args = parser.parse_args()

    root_name, folders, docs, exclusions = load_manifest()
    personas = parse_personas(args.persona)
    # A folder's `share_with: [all]` expands to every persona key actually
    # passed via --persona (see share_folder / DriveSeeder.share_folder).
    # Pass --persona all=<control-account-email> explicitly if you want a
    # dedicated "everyone" identity included too (e.g. outsider-test).

    service = None
    if not args.dry_run:
        import google.auth
        import google.auth.transport.requests
        from googleapiclient.discovery import build

        creds, _ = google.auth.default(scopes=[DRIVE_WRITE_SCOPE])
        creds.refresh(google.auth.transport.requests.Request())
        service = build("drive", "v3", credentials=creds)
        print(f"Authenticated as: {getattr(creds, 'service_account_email', '?')}")

    seeder = DriveSeeder(service, args.shared_drive_id, personas, args.dry_run)

    print(f"\n=== Seeding {root_name!r} into Shared Drive {args.shared_drive_id} ===\n")

    for folder in folders:
        folder_id = seeder.ensure_folder(folder.path)
        # "all" sharing means: share with every persona email we were given.
        share_keys = folder.share_with
        if "all" in share_keys:
            share_keys = list(personas.keys())
        seeder.share_folder(folder_id, share_keys)

    for doc in docs:
        folder_id = seeder.ensure_folder(doc.folder)
        content_path = CORPUS_DIR / doc.content_file
        if not content_path.exists():
            print(f"WARNING: missing content file for {doc.id}: {content_path}", file=sys.stderr)
            continue
        text = content_path.read_text()
        seeder.create_from_text(folder_id, doc.title, doc.target_mime, text)

    for excl in exclusions:
        folder_id = seeder.ensure_folder(excl.folder)
        if excl.kind == "unsupported_binary":
            mime = "image/jpeg" if excl.title.endswith(".jpg") else "video/mp4"
            # Tiny placeholder payload - Drive stores it under the declared
            # MIME type regardless of real bytes; we're testing that ingestion
            # skips unsupported types by MIME, not validating real media.
            seeder.create_binary_placeholder(folder_id, excl.title, mime, b"placeholder")
        elif excl.kind == "public_link_pdf":
            content_path = CORPUS_DIR / (excl.content_file or "")
            text = content_path.read_text() if content_path.exists() else excl.note
            file_id = seeder.create_from_text(folder_id, excl.title.replace(".pdf", ""), "pdf", text)
            seeder.make_public_link(file_id)

    print("\nDone." if not args.dry_run else "\nDry run complete - no API calls made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
