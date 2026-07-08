"""Django settings for the knowledge graph backend."""

import os
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

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "health",
    "integrations",
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

NEO4J_URI = env("NEO4J_URI", default="bolt://neo4j:7687")
NEO4J_USER = env("NEO4J_USER", default="neo4j")
NEO4J_PASSWORD = env("NEO4J_PASSWORD", default="change-this-neo4j-password")

GOOGLE_WORKSPACE_DOMAIN = env("GOOGLE_WORKSPACE_DOMAIN", default="")
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
