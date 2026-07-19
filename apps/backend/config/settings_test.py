"""Test settings: base settings with an in-memory SQLite database.

Selected via DJANGO_SETTINGS_MODULE in pyproject.toml's pytest section. This is
the single test-database mechanism — deterministic regardless of how pytest is
launched (pytest-xdist workers, IDE runners, CI wrappers), unlike sys.argv
sniffing or conftest environment tricks, which both have ordering failure modes
that can silently point tests at the real database.
"""

from config.settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Tests must never reach live services: Celery tasks run inline and raise.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Unit tests select provider fakes explicitly. Never let a developer's local
# .env make the test suite send document text, questions, or context off-host.
GRAPH_EMBEDDING_PROVIDER = "none"
QUERY_ANSWER_PROVIDER = "extractive"

# In-process cache so throttle tests never need a Redis instance.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
