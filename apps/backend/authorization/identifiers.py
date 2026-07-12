import hashlib


def _digest(connection_id: int, value: str) -> str:
    material = f"{connection_id}:{value.strip().lower()}".encode()
    return hashlib.sha256(material).hexdigest()


def connection_prefix(connection_id: int) -> str:
    return f"c{connection_id}_"


def document_object_id(connection_id: int, source_document_id: int) -> str:
    return f"c{connection_id}_d{source_document_id}"


def folder_object_id(connection_id: int, drive_folder_id: str) -> str:
    return f"c{connection_id}_f{_digest(connection_id, drive_folder_id)}"


def user_object_id(connection_id: int, email: str) -> str:
    return f"c{connection_id}_u{_digest(connection_id, email)}"


def group_object_id(connection_id: int, email: str) -> str:
    return f"c{connection_id}_g{_digest(connection_id, email)}"
