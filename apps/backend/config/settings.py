"""Django settings for the knowledge graph backend."""

import os
import re
import sys
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)

env_file = REPO_ROOT / ".env"
database_url_from_environment = "DATABASE_URL" in os.environ
if env_file.exists():
    environ.Env.read_env(env_file)

DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

management_commands = {Path(argument).name for argument in sys.argv}

if DEBUG or management_commands.intersection({"test", "pytest"}):
    SECRET_KEY = env("DJANGO_SECRET_KEY", default="unsafe-local-development-key")
else:
    SECRET_KEY = env("DJANGO_SECRET_KEY")
    # Fail closed on weak keys, not just missing ones (Django check W009):
    # a short or generated-default key undermines every signed artifact.
    if len(SECRET_KEY) < 50 or SECRET_KEY.startswith(("django-insecure-", "unsafe-")):
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be at least 50 random characters and not a "
            "development default. Generate one with "
            '`python -c "import secrets; print(secrets.token_urlsafe(64))"`.'
        )

    # TLS terminates at Traefik, which sets X-Forwarded-Proto; Django must
    # trust that header so the redirect below cannot loop, mark its cookies
    # secure, and bounce any plain-HTTP request that still reaches it.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # 30 days by default — long enough to matter, short enough to back out of
    # if TLS setup changes. Raise it once the deployment is proven stable.
    SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=2_592_000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
        "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
    )

    # Traefik guards the ops UIs (Dozzle, Uptime Kuma) with this basicauth
    # credential. Django never uses it, but it is the one process guaranteed
    # to boot from the same .env, so it enforces what Traefik cannot check
    # itself: the documented local default (admin/password) must never reach
    # production. "H6uskkkW" is that default hash's salt — it must match the
    # TRAEFIK_OPS_BASIC_AUTH_USERS default in .env.example and
    # infra/compose.infrastructure.yml; update all three together.
    _ops_auth_users = env("TRAEFIK_OPS_BASIC_AUTH_USERS", default="")
    if not _ops_auth_users or "H6uskkkW" in _ops_auth_users:
        raise ImproperlyConfigured(
            "TRAEFIK_OPS_BASIC_AUTH_USERS is unset or still the local-dev "
            "default (admin/password). Generate a real credential with "
            "`htpasswd -nB <user>` (escape every $ as $$ in .env) before "
            "deploying."
        )

# Needed for POSTed session-auth requests once the API is served over HTTPS
# behind Traefik, e.g. https://api.<client-domain>.
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# django.contrib.admin is deliberately absent: no ModelAdmin is registered
# anywhere, and an unused admin is pure attack surface (login form, static
# assets that are never collected). Re-add it together with real admin
# registrations and static-file serving if the project ever needs it.
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "health",
    "integrations",
    "graph",
    "authorization",
    "retrieval",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

# pytest (the supported runner) selects config.settings_test via pyproject.toml
# for an in-memory SQLite DB. A bare `manage.py test` runs against these base
# settings — pass --settings=config.settings_test if you must use it.
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://kg_user:change-this-postgres-password@postgres:5432/knowledge_graph",
    )
}
# Persistent connections: without this every request/task opens a fresh
# Postgres connection. Health checks make a recycled-by-the-server connection
# reconnect instead of erroring the first request that touches it.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DJANGO_DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Everything to stdout/stderr — the containers' log driver owns retention.
# Without an explicit config, gunicorn workers ship almost no application
# logs and errors surface only as opaque 500s.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "console"},
    },
    "root": {
        "handlers": ["console"],
        "level": env("DJANGO_LOG_LEVEL", default="INFO"),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "drive-roots": env("DRIVE_ROOTS_THROTTLE_RATE", default="30/hour"),
        "drive-sync": env("DRIVE_SYNC_THROTTLE_RATE", default="10/hour"),
        "permission-sync": env("PERMISSION_SYNC_THROTTLE_RATE", default="10/hour"),
        "query": env("QUERY_THROTTLE_RATE", default="60/hour"),
    },
}

REDIS_URL = env("REDIS_URL", default="redis://redis:6379/0")

# The DRF throttle counters live in this cache; it must be shared across
# workers (Redis), otherwise rate limits are per-process and cosmetic.
# If REDIS_URL is overridden to another host, set DJANGO_CACHE_URL alongside
# it — this default does not follow REDIS_URL.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("DJANGO_CACHE_URL", default="redis://redis:6379/2"),
    }
}
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://redis:6379/1")
CELERY_TASK_TRACK_STARTED = True
CELERY_TIMEZONE = TIME_ZONE

# A worker crash mid-task leaves the DriveSyncRun row stuck in RUNNING
# forever with no other signal that it died. The sweep is the recovery path.
DRIVE_SYNC_STALE_RUN_TIMEOUT_MINUTES = env.int("DRIVE_SYNC_STALE_RUN_TIMEOUT_MINUTES", default=120)
PERMISSION_SYNC_STALE_RUN_TIMEOUT_MINUTES = env.int(
    "PERMISSION_SYNC_STALE_RUN_TIMEOUT_MINUTES", default=120
)
# Group revocations are invisible to per-document ACL hashes, so this is the
# healthy refresh cadence. Query-time evidence expiry below remains the hard
# fail-closed bound if reconciliation repeatedly fails.
PERMISSION_SYNC_INTERVAL_SECONDS = env.int("PERMISSION_SYNC_INTERVAL_SECONDS", default=900)
PERMISSION_VERIFICATION_MAX_AGE_SECONDS = env.int(
    "PERMISSION_VERIFICATION_MAX_AGE_SECONDS", default=1800
)
if PERMISSION_SYNC_INTERVAL_SECONDS < 1:
    raise ImproperlyConfigured("PERMISSION_SYNC_INTERVAL_SECONDS must be positive.")
if PERMISSION_VERIFICATION_MAX_AGE_SECONDS <= PERMISSION_SYNC_INTERVAL_SECONDS:
    raise ImproperlyConfigured(
        "PERMISSION_VERIFICATION_MAX_AGE_SECONDS must be greater than "
        "PERMISSION_SYNC_INTERVAL_SECONDS."
    )
CELERY_BEAT_SCHEDULE = {
    "schedule-permission-syncs": {
        "task": "integrations.schedule_permission_syncs",
        "schedule": float(PERMISSION_SYNC_INTERVAL_SECONDS),
    },
    "sweep-stale-drive-sync-runs": {
        "task": "integrations.sweep_stale_drive_sync_runs",
        "schedule": 900.0,
    },
    "sweep-stale-permission-sync-runs": {
        "task": "integrations.sweep_stale_permission_sync_runs",
        "schedule": 900.0,
    },
    "sweep-stale-graph-extractions": {
        "task": "integrations.sweep_stale_graph_extractions",
        "schedule": 900.0,
    },
}

SPICEDB_GRPC_URL = env("SPICEDB_GRPC_URL", default="spicedb:50051")
SPICEDB_GRPC_PRESHARED_KEY = env("SPICEDB_GRPC_PRESHARED_KEY", default="change-this-spicedb-key")
_development_context = DEBUG or bool(management_commands.intersection({"test", "pytest"}))
if not _development_context and SPICEDB_GRPC_PRESHARED_KEY == "change-this-spicedb-key":
    raise ImproperlyConfigured(
        "SPICEDB_GRPC_PRESHARED_KEY must not use the development default outside development."
    )
# Plaintext gRPC sends the preshared key and every permission tuple in the
# clear; outside development that requires an explicit private-network waiver.
SPICEDB_GRPC_TLS = env.bool("SPICEDB_GRPC_TLS", default=False)
SPICEDB_GRPC_ALLOW_INSECURE = env.bool("SPICEDB_GRPC_ALLOW_INSECURE", default=False)
if not _development_context and not SPICEDB_GRPC_TLS and not SPICEDB_GRPC_ALLOW_INSECURE:
    raise ImproperlyConfigured(
        "Enable SPICEDB_GRPC_TLS outside development, or acknowledge a "
        "private-network deployment with SPICEDB_GRPC_ALLOW_INSECURE=true."
    )
SPICEDB_REQUEST_TIMEOUT_SECONDS = env.int("SPICEDB_REQUEST_TIMEOUT_SECONDS", default=10)
SPICEDB_BATCH_SIZE = env.int("SPICEDB_BATCH_SIZE", default=500)
if SPICEDB_REQUEST_TIMEOUT_SECONDS < 1:
    raise ImproperlyConfigured("SPICEDB_REQUEST_TIMEOUT_SECONDS must be positive.")
if not 1 <= SPICEDB_BATCH_SIZE <= 1000:
    raise ImproperlyConfigured("SPICEDB_BATCH_SIZE must be between 1 and 1000.")

NEO4J_URI = env("NEO4J_URI", default="bolt://neo4j:7687")
NEO4J_USER = env("NEO4J_USER", default="neo4j")
NEO4J_PASSWORD = env("NEO4J_PASSWORD", default="change-this-neo4j-password")
GRAPH_CHUNK_VECTOR_INDEX_NAME = env(
    "GRAPH_CHUNK_VECTOR_INDEX_NAME", default="chunk_embedding_vector"
)
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", GRAPH_CHUNK_VECTOR_INDEX_NAME):
    raise ImproperlyConfigured(
        "GRAPH_CHUNK_VECTOR_INDEX_NAME must be a safe Neo4j identifier "
        "(letters, numbers, underscores; cannot start with a number)."
    )
GRAPH_CHUNK_EMBEDDING_DIMENSIONS = env.int("GRAPH_CHUNK_EMBEDDING_DIMENSIONS", default=1536)
if GRAPH_CHUNK_EMBEDDING_DIMENSIONS < 1:
    raise ImproperlyConfigured("GRAPH_CHUNK_EMBEDDING_DIMENSIONS must be a positive integer.")
GRAPH_CHUNK_VECTOR_SIMILARITY = env("GRAPH_CHUNK_VECTOR_SIMILARITY", default="cosine")
if GRAPH_CHUNK_VECTOR_SIMILARITY not in {"cosine", "euclidean"}:
    raise ImproperlyConfigured(
        "GRAPH_CHUNK_VECTOR_SIMILARITY must be 'cosine' or 'euclidean', "
        f"got {GRAPH_CHUNK_VECTOR_SIMILARITY!r}."
    )

# Extraction engine selection (ADR-010). "paragraph" is the deterministic
# no-LLM baseline; "neo4j_graphrag" enables LLM entity/relationship
# extraction through OpenRouter and requires the key + model below.
GRAPH_EXTRACTION_ENGINE = env("GRAPH_EXTRACTION_ENGINE", default="paragraph")
if GRAPH_EXTRACTION_ENGINE not in {"paragraph", "neo4j_graphrag"}:
    raise ImproperlyConfigured(
        f"GRAPH_EXTRACTION_ENGINE must be 'paragraph' or 'neo4j_graphrag', "
        f"got {GRAPH_EXTRACTION_ENGINE!r}."
    )
GRAPH_EXTRACTION_CHUNK_MAX_CHARS = env.int("GRAPH_EXTRACTION_CHUNK_MAX_CHARS", default=12_000)
if GRAPH_EXTRACTION_CHUNK_MAX_CHARS < 1:
    raise ImproperlyConfigured("GRAPH_EXTRACTION_CHUNK_MAX_CHARS must be positive.")
GRAPH_EXTRACTION_CHUNK_OVERLAP_CHARS = env.int(
    "GRAPH_EXTRACTION_CHUNK_OVERLAP_CHARS", default=1_000
)
if not 0 <= GRAPH_EXTRACTION_CHUNK_OVERLAP_CHARS < GRAPH_EXTRACTION_CHUNK_MAX_CHARS:
    raise ImproperlyConfigured(
        "GRAPH_EXTRACTION_CHUNK_OVERLAP_CHARS must be non-negative and smaller than "
        "GRAPH_EXTRACTION_CHUNK_MAX_CHARS."
    )
# Extraction recovery. A crashed worker can leave graph_extraction_status
# stuck in RUNNING once the broker no longer holds the message — the sweep is
# the recovery path (same failure mode as DRIVE_SYNC_STALE_RUN_TIMEOUT_MINUTES
# covers for sync runs). The attempts cap bounds sync-driven requeues of the
# same content version; the pending window keeps overlapping syncs from
# double-enqueueing a job a worker simply hasn't started yet.
GRAPH_EXTRACTION_STALE_RUNNING_TIMEOUT_MINUTES = env.int(
    "GRAPH_EXTRACTION_STALE_RUNNING_TIMEOUT_MINUTES", default=60
)
if GRAPH_EXTRACTION_STALE_RUNNING_TIMEOUT_MINUTES < 1:
    raise ImproperlyConfigured("GRAPH_EXTRACTION_STALE_RUNNING_TIMEOUT_MINUTES must be positive.")
GRAPH_EXTRACTION_MAX_SYNC_ATTEMPTS = env.int("GRAPH_EXTRACTION_MAX_SYNC_ATTEMPTS", default=5)
if GRAPH_EXTRACTION_MAX_SYNC_ATTEMPTS < 1:
    raise ImproperlyConfigured("GRAPH_EXTRACTION_MAX_SYNC_ATTEMPTS must be positive.")
GRAPH_EXTRACTION_PENDING_REQUEUE_AFTER_MINUTES = env.int(
    "GRAPH_EXTRACTION_PENDING_REQUEUE_AFTER_MINUTES", default=15
)
if GRAPH_EXTRACTION_PENDING_REQUEUE_AFTER_MINUTES < 1:
    raise ImproperlyConfigured("GRAPH_EXTRACTION_PENDING_REQUEUE_AFTER_MINUTES must be positive.")
# Upper bound on concurrent per-chunk LLM calls within one document's
# extraction — chunk extractions are independent, but the provider rate
# limit is shared.
GRAPH_EXTRACTION_MAX_CONCURRENT_LLM_CALLS = env.int(
    "GRAPH_EXTRACTION_MAX_CONCURRENT_LLM_CALLS", default=4
)
if GRAPH_EXTRACTION_MAX_CONCURRENT_LLM_CALLS < 1:
    raise ImproperlyConfigured("GRAPH_EXTRACTION_MAX_CONCURRENT_LLM_CALLS must be positive.")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
OPENROUTER_BASE_URL = env("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
OPENROUTER_SITE_URL = env("OPENROUTER_SITE_URL", default="")
OPENROUTER_APP_NAME = env("OPENROUTER_APP_NAME", default="Client Knowledge Graph MVP")
OPENROUTER_REQUEST_TIMEOUT_SECONDS = env.float("OPENROUTER_REQUEST_TIMEOUT_SECONDS", default=60.0)
if OPENROUTER_REQUEST_TIMEOUT_SECONDS <= 0:
    raise ImproperlyConfigured("OPENROUTER_REQUEST_TIMEOUT_SECONDS must be positive.")

# Embeddings are opt-in so deployments can keep the deterministic Phase 3
# baseline while credentials are being provisioned. The production Phase 5
# path uses OpenRouter for both stored chunk and query embeddings.
GRAPH_EMBEDDING_PROVIDER = env("GRAPH_EMBEDDING_PROVIDER", default="none")
if GRAPH_EMBEDDING_PROVIDER not in {"none", "openrouter"}:
    raise ImproperlyConfigured(
        "GRAPH_EMBEDDING_PROVIDER must be 'none' or 'openrouter', "
        f"got {GRAPH_EMBEDDING_PROVIDER!r}."
    )
OPENROUTER_EMBEDDING_MODEL = env("OPENROUTER_EMBEDDING_MODEL", default="")
GRAPH_EMBEDDING_BATCH_SIZE = env.int("GRAPH_EMBEDDING_BATCH_SIZE", default=64)
if not 1 <= GRAPH_EMBEDDING_BATCH_SIZE <= 256:
    raise ImproperlyConfigured("GRAPH_EMBEDDING_BATCH_SIZE must be between 1 and 256.")
if GRAPH_EMBEDDING_PROVIDER == "openrouter" and not (
    OPENROUTER_API_KEY and OPENROUTER_EMBEDDING_MODEL
):
    raise ImproperlyConfigured(
        "GRAPH_EMBEDDING_PROVIDER='openrouter' requires OPENROUTER_API_KEY "
        "and OPENROUTER_EMBEDDING_MODEL to be set."
    )

# Answer synthesis is independently selectable so enabling graph extraction
# or embeddings cannot silently start sending retrieval context to an LLM.
QUERY_ANSWER_PROVIDER = env("QUERY_ANSWER_PROVIDER", default="extractive")
if QUERY_ANSWER_PROVIDER not in {"extractive", "openrouter"}:
    raise ImproperlyConfigured(
        "QUERY_ANSWER_PROVIDER must be 'extractive' or 'openrouter', "
        f"got {QUERY_ANSWER_PROVIDER!r}."
    )
OPENROUTER_MODEL = env("OPENROUTER_MODEL", default="")
QUERY_CONTEXT_MAX_CHARS = env.int("QUERY_CONTEXT_MAX_CHARS", default=12_000)
QUERY_RETRIEVAL_LIMIT = env.int("QUERY_RETRIEVAL_LIMIT", default=5)
QUERY_VECTOR_MIN_SCORE = env.float("QUERY_VECTOR_MIN_SCORE", default=0.45)
if QUERY_CONTEXT_MAX_CHARS < 1:
    raise ImproperlyConfigured("QUERY_CONTEXT_MAX_CHARS must be positive.")
if not 1 <= QUERY_RETRIEVAL_LIMIT <= 20:
    raise ImproperlyConfigured("QUERY_RETRIEVAL_LIMIT must be between 1 and 20.")
if not 0.0 <= QUERY_VECTOR_MIN_SCORE <= 1.0:
    raise ImproperlyConfigured("QUERY_VECTOR_MIN_SCORE must be between 0 and 1.")
if QUERY_ANSWER_PROVIDER == "openrouter" and not (OPENROUTER_API_KEY and OPENROUTER_MODEL):
    raise ImproperlyConfigured(
        "QUERY_ANSWER_PROVIDER='openrouter' requires OPENROUTER_API_KEY "
        "and OPENROUTER_MODEL to be set."
    )

GRAPH_EXTRACTION_MODEL = env("GRAPH_EXTRACTION_MODEL", default="")
if GRAPH_EXTRACTION_ENGINE == "neo4j_graphrag" and not (
    OPENROUTER_API_KEY and GRAPH_EXTRACTION_MODEL
):
    raise ImproperlyConfigured(
        "GRAPH_EXTRACTION_ENGINE='neo4j_graphrag' requires OPENROUTER_API_KEY "
        "and GRAPH_EXTRACTION_MODEL to be set."
    )

GOOGLE_WORKSPACE_DOMAIN = env("GOOGLE_WORKSPACE_DOMAIN", default="")
GOOGLE_DRIVE_AUTH_MODE = env("GOOGLE_DRIVE_AUTH_MODE", default="service_account")
if GOOGLE_DRIVE_AUTH_MODE not in {"service_account", "oauth_dev"}:
    raise ImproperlyConfigured("GOOGLE_DRIVE_AUTH_MODE must be 'service_account' or 'oauth_dev'.")
if GOOGLE_DRIVE_AUTH_MODE == "oauth_dev" and not _development_context:
    raise ImproperlyConfigured(
        "GOOGLE_DRIVE_AUTH_MODE='oauth_dev' is permitted only in development/test context."
    )
GOOGLE_OAUTH_CLIENT_SECRET_FILE = env("GOOGLE_OAUTH_CLIENT_SECRET_FILE", default="")
GOOGLE_OAUTH_TOKEN_FILE = env("GOOGLE_OAUTH_TOKEN_FILE", default="")
GOOGLE_SERVICE_ACCOUNT_FILE = env("GOOGLE_SERVICE_ACCOUNT_FILE", default="")
GOOGLE_DRIVE_DELEGATED_SUBJECT = env("GOOGLE_DRIVE_DELEGATED_SUBJECT", default="")
GOOGLE_DRIVE_SCOPE_TYPE = env("GOOGLE_DRIVE_SCOPE_TYPE", default="folder")
# Must stay in sync with integrations.models.DriveConnection.ScopeType (models
# can't be imported here). Fail at startup, not at the first model save.
if GOOGLE_DRIVE_SCOPE_TYPE not in {"folder", "shared_drive"}:
    raise ImproperlyConfigured(
        f"GOOGLE_DRIVE_SCOPE_TYPE must be 'folder' or 'shared_drive', "
        f"got {GOOGLE_DRIVE_SCOPE_TYPE!r}."
    )
GOOGLE_DRIVE_ROOT_ID = env("GOOGLE_DRIVE_ROOT_ID", default="")
GOOGLE_SHARED_DRIVE_ID = env("GOOGLE_SHARED_DRIVE_ID", default="")
