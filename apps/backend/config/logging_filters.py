"""Logging filters for authentication callback request lines."""

import logging
import re

_OAUTH_CALLBACK_QUERY = re.compile(r"(/api/(?:session/google|drive/oauth)/callback)\?[^ ]+")


class RedactOAuthCallbackQuery(logging.Filter):
    """Strip one-time OAuth values from development-server access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            first = record.args[0]
            if isinstance(first, str):
                sanitized = _OAUTH_CALLBACK_QUERY.sub(r"\1?redacted=1", first)
                if sanitized != first:
                    record.args = (sanitized, *record.args[1:])
        return True
