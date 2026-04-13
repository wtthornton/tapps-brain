# Makefile — tapps-brain developer workflow
#
# All targets assume you are in the repository root (where pyproject.toml lives).
#
# Quick start:
#   make brain-up    → start Postgres+pgvector
#   make brain-test  → run full test suite
#   make brain-down  → tear down
#
# See docs/guides/postgres-dsn.md for all env-var options.

COMPOSE       := docker compose
HIVE_COMPOSE  := docker compose -f docker/docker-compose.hive.yaml
PYTEST        := uv run pytest
RUFF          := uv run ruff
MYPY          := uv run mypy

# DSN used by brain-test and brain-psql
TAPPS_DEV_DSN ?= postgres://tapps:tapps@localhost:5432/tapps_dev

.PHONY: help brain-up brain-down brain-restart brain-migrate brain-test brain-test-fast \
        brain-lint brain-type brain-qa brain-psql \
        hive-build hive-deploy hive-up hive-down hive-logs

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Docker / Postgres lifecycle
# ---------------------------------------------------------------------------

brain-up:  ## Start Postgres+pgvector in the background (docker-compose.yml)
	$(COMPOSE) up -d
	@echo "Waiting for Postgres to be ready…"
	@$(COMPOSE) exec tapps-db sh -c \
	  'for i in $$(seq 1 30); do pg_isready -U tapps -d tapps_dev && exit 0; sleep 1; done; echo "Postgres did not become ready in time"; exit 1'
	@echo "Postgres is ready. DSN: $(TAPPS_DEV_DSN)"

brain-down:  ## Stop containers and remove volumes (destructive)
	$(COMPOSE) down -v

brain-restart:  ## Restart the Postgres container (keeps volumes)
	$(COMPOSE) restart tapps-db

brain-psql:  ## Open a psql shell in the running tapps-db container
	$(COMPOSE) exec tapps-db psql -U tapps -d tapps_dev

brain-migrate:  ## Apply all pending schema migrations (private, hive, federation)
	TAPPS_BRAIN_DATABASE_URL=$(TAPPS_DEV_DSN) \
	  uv run python scripts/apply_all_migrations.py

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

brain-test:  ## Full test suite with coverage (requires brain-up + brain-migrate, or external DSN)
	TAPPS_TEST_POSTGRES_DSN=$(TAPPS_DEV_DSN) \
	  $(PYTEST) tests/ -v --tb=short \
	    -m "not benchmark" \
	    --cov=tapps_brain \
	    --cov-report=term-missing \
	    --cov-fail-under=95

brain-test-fast:  ## Tests excluding slow/benchmark, no coverage (rapid iteration)
	TAPPS_TEST_POSTGRES_DSN=$(TAPPS_DEV_DSN) \
	  $(PYTEST) tests/ --tb=short -q -m "not benchmark and not slow" -x

# ---------------------------------------------------------------------------
# Lint / type
# ---------------------------------------------------------------------------

brain-lint:  ## Ruff lint + format check
	$(RUFF) check src/ tests/
	$(RUFF) format --check src/ tests/

brain-type:  ## Strict mypy type check
	$(MYPY) --strict src/tapps_brain/

brain-qa:  ## Full QA: lint + type + migrations + tests (mirrors CI)
	$(MAKE) brain-lint
	$(MAKE) brain-type
	$(MAKE) brain-migrate
	$(MAKE) brain-test

# ---------------------------------------------------------------------------
# Local hive (Docker) deployment
# ---------------------------------------------------------------------------

hive-build:  ## Build wheel + Docker images (cleans dist first to avoid stale wheels)
	rm -f dist/*.whl dist/*.tar.gz
	uv build
	$(HIVE_COMPOSE) build

hive-deploy:  ## Full deploy to local Docker: build → migrate → restart (safe to rerun)
	$(MAKE) hive-build
	$(HIVE_COMPOSE) run --rm tapps-hive-migrate
	$(HIVE_COMPOSE) up -d tapps-visual

hive-up:  ## Start hive services without rebuilding
	$(HIVE_COMPOSE) up -d tapps-hive-db tapps-visual

hive-down:  ## Stop hive containers (keeps volumes)
	$(HIVE_COMPOSE) down

hive-logs:  ## Tail logs from running hive services
	$(HIVE_COMPOSE) logs -f
