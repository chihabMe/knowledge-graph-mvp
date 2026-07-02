COMPOSE = docker compose -f infra/compose.infrastructure.yml -f infra/compose.app.yml
BACKEND_DIR = apps/backend
CORE_SERVICES = postgres redis neo4j spicedb django celery-worker

.PHONY: config up up-all down logs migrate django-check test lint format health smoke review review-branch install-hooks

config:
	docker compose -f infra/compose.infrastructure.yml config >/tmp/kg-infra-compose-check.txt
	$(COMPOSE) config >/tmp/kg-combined-compose-check.txt

up:
	$(COMPOSE) up -d --build $(CORE_SERVICES)

up-all:
	$(COMPOSE) --profile scheduler up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=120

migrate:
	$(COMPOSE) run --rm --no-deps django python manage.py migrate --noinput

django-check:
	cd $(BACKEND_DIR) && uv run python manage.py check

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

smoke:
	$(COMPOSE) exec -T django python -c "import urllib.request; req=urllib.request.Request('http://127.0.0.1:8000/api/tasks/smoke-test/', method='POST'); print(urllib.request.urlopen(req).read().decode())"

# Senior engineer review — reviews the last commit
review:
	bash scripts/review-commit.sh HEAD~1 HEAD

# Reviews everything on the current branch vs main
review-branch:
	bash scripts/review-commit.sh origin/main HEAD

# Install git hooks (run once after cloning)
install-hooks:
	bash scripts/install-hooks.sh
