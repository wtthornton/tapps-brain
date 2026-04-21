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
# Full stack (Postgres + unified tapps-brain-http + migrate + dashboard).
# Project name `tapps-brain` keeps the network name `tapps-brain_default`,
# which AgentForge and other consumers resolve by DNS.
HIVE_COMPOSE  := docker compose -p tapps-brain -f docker/docker-compose.hive.yaml
PYTEST        := uv run pytest
RUFF          := uv run ruff
MYPY          := uv run mypy

BRAIN_VERSION ?= $(shell grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')
BRAIN_IMAGE   ?= docker-tapps-brain-http

# DSN used by brain-test and brain-psql (dev-only Postgres from docker-compose.yml)
TAPPS_DEV_DSN ?= postgres://tapps:tapps@localhost:5432/tapps_brain_dev

.PHONY: help brain-up brain-down brain-restart brain-migrate brain-test brain-test-fast \
        brain-lint brain-type brain-qa brain-psql brain-healthcheck \
        hive-build hive-deploy hive-up hive-down hive-logs hive-smoke check-brain-env \
        publish-brain-image

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Docker / Postgres lifecycle
# ---------------------------------------------------------------------------

brain-up:  ## Start dev Postgres+pgvector in the background (docker-compose.yml)
	$(COMPOSE) up -d
	@echo "Waiting for Postgres to be ready…"
	@$(COMPOSE) exec tapps-brain-db sh -c \
	  'for i in $$(seq 1 30); do pg_isready -U tapps -d tapps_brain_dev && exit 0; sleep 1; done; echo "Postgres did not become ready in time"; exit 1'
	@echo "Postgres is ready. DSN: $(TAPPS_DEV_DSN)"

brain-down:  ## Stop dev containers and remove volumes (destructive)
	$(COMPOSE) down -v

brain-restart:  ## Restart the dev Postgres container (keeps volumes)
	$(COMPOSE) restart tapps-brain-db

brain-psql:  ## Open a psql shell in the running dev Postgres container
	$(COMPOSE) exec tapps-brain-db psql -U tapps -d tapps_brain_dev

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
# Unified tapps-brain Docker deployment
#
# The `hive-*` target names are kept as aliases for backward compatibility
# with user scripts, but what they deploy is the unified tapps-brain stack:
# one Postgres + one tapps-brain-http container (serves private memory + Hive
# + Federation on the same /mcp/ + /v1/* API) + an nginx dashboard. Hive is
# a feature of tapps-brain, not a separate service (ADR-007).
#
# Required env in docker/.env (see docker/.env.example):
#   TAPPS_BRAIN_DB_PASSWORD, TAPPS_BRAIN_AUTH_TOKEN, TAPPS_BRAIN_ADMIN_TOKEN
# ---------------------------------------------------------------------------

hive-build:  ## Build wheel + Docker images for the unified tapps-brain stack
	rm -f dist/*.whl dist/*.tar.gz
	uv build
	$(HIVE_COMPOSE) build

check-brain-env:  ## Abort if docker/.env is missing or has placeholder values
	@if [ ! -f docker/.env ]; then \
	  echo ""; \
	  echo "ERROR: docker/.env is missing."; \
	  echo "       Copy the template and fill in strong random values:"; \
	  echo "         cp docker/.env.example docker/.env"; \
	  echo "         \$$EDITOR docker/.env"; \
	  echo ""; \
	  exit 1; \
	fi
	@if grep -q 'REPLACE_ME' docker/.env; then \
	  echo ""; \
	  echo "ERROR: docker/.env still contains REPLACE_ME placeholder values."; \
	  echo "       Generate real tokens:"; \
	  echo "         openssl rand -base64 32   # for TAPPS_BRAIN_DB_PASSWORD"; \
	  echo "         openssl rand -hex 32      # for TAPPS_BRAIN_AUTH_TOKEN + _ADMIN_TOKEN"; \
	  echo ""; \
	  exit 1; \
	fi

hive-deploy:  ## Full deploy: check env → build → migrate → up. Safe to rerun.
	$(MAKE) check-brain-env
	$(MAKE) hive-build
	$(HIVE_COMPOSE) up -d

hive-up:  ## Start the unified brain stack without rebuilding
	$(MAKE) check-brain-env
	$(HIVE_COMPOSE) up -d

hive-down:  ## Stop brain containers (keeps volumes — data preserved)
	$(HIVE_COMPOSE) down

hive-logs:  ## Tail logs from running brain services
	$(HIVE_COMPOSE) logs -f

hive-smoke:  ## End-to-end stack smoke test (boots full stack, asserts endpoints, tears down)
	@bash scripts/hive_smoke.sh

brain-healthcheck:  ## Verify this repo is wired to the deployed tapps-brain and MCP tools work
	@bash scripts/brain-healthcheck.sh

publish-brain-image:  ## Build wheel + docker-tapps-brain-http:latest (called by AgentForge brain-build)
	rm -f dist/*.whl dist/*.tar.gz
	uv build
	docker build \
	  --build-arg TAPPS_BRAIN_VERSION=$(BRAIN_VERSION) \
	  -f docker/Dockerfile.http \
	  -t $(BRAIN_IMAGE):latest \
	  -t $(BRAIN_IMAGE):$(BRAIN_VERSION) \
	  .
