"""Content export/download for supported Drive files.

Google-native formats are exported to text-shaped formats; uploaded files
(PDFs etc.) are downloaded as-is. Returns raw bytes plus the effective MIME
type — persistence and hashing decisions live with the caller.
"""

import hashlib

# Google-native formats and the export target for each. Everything else that
# reaches the exporter is downloaded unchanged.
GOOGLE_EXPORT_MIME_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}


def export_file_content(service, *, drive_file_id: str, mime_type: str) -> tuple[bytes, str]:
    """Return (content_bytes, effective_mime_type) for one Drive file."""
    export_mime = GOOGLE_EXPORT_MIME_TYPES.get(mime_type)
    if export_mime:
        data = (
            service.files()
            .export(fileId=drive_file_id, mimeType=export_mime)
            .execute()
        )
        return _as_bytes(data), export_mime
    data = (
        service.files()
        .get_media(fileId=drive_file_id, supportsAllDrives=True)
        .execute()
    )
    return _as_bytes(data), mime_type


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _as_bytes(data) -> bytes:
    if isinstance(data, bytes):
        return data
    return str(data).encode("utf-8")
