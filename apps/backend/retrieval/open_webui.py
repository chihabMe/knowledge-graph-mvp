import json
import time
import uuid
from urllib.parse import urlsplit

from retrieval.services import QueryResult

TRUSTED_GOOGLE_CITATION_HOSTS = frozenset({"drive.google.com", "docs.google.com"})


def _markdown_text(value) -> str:
    text = str(value)
    for character in ("\\", "[", "]"):
        text = text.replace(character, f"\\{character}")
    return text.replace("\r", " ").replace("\n", " ").strip()


def _trusted_drive_url(value) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2_048
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in TRUSTED_GOOGLE_CITATION_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            return None
    except ValueError:
        return None
    return value.replace("<", "%3C").replace(">", "%3E")


def render_chat_content(result: QueryResult) -> str:
    """Render only server-owned permitted citations into standard chat text."""
    if result.refused:
        return result.answer

    source_lines = []
    for citation in result.citations:
        drive_url = _trusted_drive_url(citation.get("drive_url"))
        title = _markdown_text(citation.get("title", ""))
        if not drive_url or not title:
            continue
        source_lines.append(f"{len(source_lines) + 1}. [{title}](<{drive_url}>)")
    if not source_lines:
        return result.answer
    return f"{result.answer}\n\nSources:\n" + "\n".join(source_lines)


def build_chat_completion_payload(result: QueryResult, model_id: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": render_chat_content(result),
                },
                "finish_reason": "stop",
            }
        ],
    }


def build_buffered_chat_completion_events(
    result: QueryResult,
    model_id: str,
) -> tuple[str, ...]:
    """Return SSE only after the complete permission-safe result exists.

    The answer is deliberately emitted as one buffered delta. Nothing from an
    upstream provider is streamed across the authorization or citation gates.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    content_event = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": render_chat_content(result),
                },
                "finish_reason": None,
            }
        ],
    }
    finish_event = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }

    def encode(payload: dict) -> str:
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"

    return encode(content_event), encode(finish_event), "data: [DONE]\n\n"
