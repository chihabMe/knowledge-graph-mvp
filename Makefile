# Compose resolves ${...} interpolation from the .env next to the first -f
# file (infra/), NOT the repo root. Runtime commands deliberately point at the
# real root .env and still fail when it is absent. Config-only validation may
# safely fall back to .env.example because it never starts a service.
REPO_ROOT = $(abspath .)
RUNTIME_ENV_FILE = $(REPO_ROOT)/.env
CONFIG_ENV_FILE = $(if $(wildcard .env),$(RUNTIME_ENV_FILE),$(REPO_ROOT)/.env.example)
COMPOSE_ENV_FLAG = $(if $(wildcard .env),--env-file $(RUNTIME_ENV_FILE))
COMPOSE_BASE = APP_ENV_FILE=$(RUNTIME_ENV_FILE) docker compose $(COMPOSE_ENV_FLAG) -f infra/compose.infrastructure.yml -f infra/compose.app.yml
CONFIG_COMPOSE = APP_ENV_FILE=$(CONFIG_ENV_FILE) docker compose --env-file $(CONFIG_ENV_FILE)
COMPOSE_DEV = $(COMPOSE_BASE) -f infra/compose.dev.yml
COMPOSE_PROD = $(COMPOSE_BASE)
# Interactive development is the default. Production commands opt into the
# hardened image explicitly so local source edits never require a rebuild.
COMPOSE = $(COMPOSE_DEV)
BACKEND_DIR = apps/backend
CORE_SERVICES = postgres redis neo4j spicedb django celery-worker

.PHONY: config up up-prod up-all up-all-prod down logs migrate migration-check django-check test lint format health smoke review-staged review review-branch install-hooks

config:
	$(CONFIG_COMPOSE) -f infra/compose.infrastructure.yml config >/tmp/kg-infra-compose-check.txt
	$(CONFIG_COMPOSE) -f infra/compose.infrastructure.yml -f infra/compose.app.yml config >/tmp/kg-prod-compose-check.txt
	$(CONFIG_COMPOSE) -f infra/compose.infrastructure.yml -f infra/compose.app.yml -f infra/compose.dev.yml config >/tmp/kg-dev-compose-check.txt

up:
	$(COMPOSE) up -d --build $(CORE_SERVICES)

up-prod:
	$(COMPOSE_PROD) up -d --build $(CORE_SERVICES)

up-all:
	$(COMPOSE) --profile scheduler up -d --build

up-all-prod:
	$(COMPOSE_PROD) --profile scheduler up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=120

migrate:
	$(COMPOSE) run --rm --no-deps django python manage.py migrate --noinput

migration-check:
	cd $(BACKEND_DIR) && DJANGO_DEBUG=true uv run python manage.py makemigrations --check --dry-run --settings=config.settings_test

django-check:
	cd $(BACKEND_DIR) && DJANGO_DEBUG=true uv run python manage.py check

test:
	cd $(BACKEND_DIR) && uv run pytest

lint:
	cd $(BACKEND_DIR) && uv run ruff check .
	cd $(BACKEND_DIR) && uv run ruff format --check .

format:
	cd $(BACKEND_DIR) && uv run ruff format .

health:
	@for attempt in $$(seq 1 30); do \
		if $(COMPOSE) exec -T django python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health/', timeout=10).read().decode())"; then \
			exit 0; \
		fi; \
		sleep 2; \
	done; \
	exit 1

# The HTTP endpoint is admin-only now, so the smoke check enqueues directly.
smoke:
	$(COMPOSE) exec -T django python manage.py shell -c "from core.tasks import smoke_test; print('queued:', smoke_test.delay().id)"

# Deterministic staged checks without creating a commit.
review-staged:
	@chmod +x scripts/hooks/pre-commit scripts/review-staged.sh 2>/dev/null || true
	bash scripts/review-staged.sh

# Optional AI review helper; set ENABLE_AI_REVIEW=1 to call Claude.
review:
	bash scripts/review-commit.sh HEAD~1 HEAD

# Optional branch AI review helper; set ENABLE_AI_REVIEW=1 to call Claude.
review-branch:
	bash scripts/review-commit.sh origin/main HEAD

# Install git hooks (run once after cloning)
install-hooks:
	bash scripts/install-hooks.sh
