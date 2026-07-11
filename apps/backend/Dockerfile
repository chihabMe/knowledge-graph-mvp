# Shared foundation: runtime env, unprivileged user. No package manager or
# compiler ever reaches the final images.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN groupadd --system django \
    && useradd --system --gid django --home /app django

# Dependency builder. uv is pinned and copied from its distroless image, so
# builds are reproducible and uv never ships in a runtime stage. --frozen is
# deliberate and has no fallback: a stale uv.lock must fail the build, not
# silently re-resolve to newer packages.
FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.22 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

# Development target: dev dependency group on top of the builder venv, the
# autoreloading dev server, and (via infra/compose.dev.yml) bind-mounted
# source. Never deploy this stage.
FROM builder AS dev

RUN uv sync --frozen

COPY . .

USER django

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# Production target (default: last stage). Source and venv stay root-owned —
# the app user can read and execute but never mutate the deployed code.
# celery-beat therefore writes its schedule file to /tmp (see compose).
FROM base AS prod

COPY --from=builder /app/.venv /app/.venv

COPY . .

USER django

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
