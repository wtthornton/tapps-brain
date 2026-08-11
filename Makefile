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

# Dev pytest Postgres (docker-compose.yml). Project `tapps-brain-dev` keeps the
# dev DB off `tapps-brain_default` so `tapps-brain-db` DNS stays hive-only
# (EPIC-076 / STORY-076.1).
DEV_COMPOSE   := docker compose -p tapps-brain-dev
# Full stack (Postgres + unified tapps-brain-http + migrate + dashboard).
# Project name `tapps-brain` keeps the network name `tapps-brain_default`,
# which AgentForge and other consumers resolve by DNS.
HIVE_COMPOSE  := docker compose -p tapps-brain -f docker/docker-compose.hive.yaml
PYTEST        := uv run pytest
RUFF          := uv run ruff
MYPY          := uv run mypy

# Faster Docker rebuilds (pip cache mounts in Dockerfiles.http / Dockerfile.migrate).
export DOCKER_BUILDKIT ?= 1

# Parallel workers for brain-test-fast (-n auto). Set BRAIN_TEST_FAST_N=0 to disable.
BRAIN_TEST_FAST_N ?= auto
PYTEST_XDIST_FLAG := $(if $(filter 0,$(BRAIN_TEST_FAST_N)),,-n $(BRAIN_TEST_FAST_N))

# Export so `$(HIVE_COMPOSE)` (build/up) sees the pyproject-derived version in
# its environment, which overrides any stale BRAIN_VERSION in docker/.env. Without
# this, plain `make hive-deploy` tags/runs images at the .env value, not the real
# release version (e.g. 3.22.0 instead of 3.23.0).
BRAIN_VERSION ?= $(shell grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')
export BRAIN_VERSION
BRAIN_IMAGE   ?= docker-tapps-brain-http

# DSN used by brain-test, brain-migrate, purge-test-tenants (dev Postgres).
# Port must track docker-compose.yml ``${TAPPS_DEV_PORT:-5432}:5432`` — a hard-coded
# 5432 silently targets the wrong container when TAPPS_DEV_PORT is remapped.
# `check-dev-dsn` enforces that agreement instead of trusting it (TAP-5846):
# 5432 is occupied on most dev hosts, so `TAPPS_DEV_PORT=55432 make brain-up`
# followed by a bare `make brain-migrate` would otherwise migrate a stranger's
# database without a word.
TAPPS_DEV_PORT ?= 5432
TAPPS_DEV_DSN ?= postgres://tapps:tapps@localhost:$(TAPPS_DEV_PORT)/tapps_brain_dev

# Password provisioned for the dev-only `tapps_runtime` role by `brain-roles`.
# Load-bearing: the RLS/tenant integration tests build their runtime DSN by
# string-replacing `tapps:tapps@` with `tapps_runtime:tapps_runtime@`
# (tests/integration/test_tenant_isolation.py, test_session_context_persistence.py,
# test_kg_predicate_registry_isolation.py), so this must stay in sync with them.
# Dev only. Production sets TAPPS_BRAIN_RUNTIME_PASSWORD via the migrate sidecar.
TAPPS_DEV_RUNTIME_PASSWORD ?= tapps_runtime

.PHONY: help brain-up brain-down brain-restart brain-migrate brain-roles check-dev-dsn \
        brain-test brain-test-fast \
        brain-lint brain-type brain-qa brain-psql brain-healthcheck brain-smoke-live \
        brain-visual-smoke-live brain-diagnostics-live brain-eval purge-test-tenants \
        hive-wheel hive-build hive-deploy hive-reload-http hive-reload dev-deploy \
        hive-up hive-down hive-logs hive-smoke check-brain-env brain-env-init \
        check-compose-isolation publish-brain-image

# Abort when the dev Postgres container is on tapps-brain_default (pre-076.1 layout
# or bare `docker compose up` without -p tapps-brain-dev). Set BRAIN_FORCE=1 to skip.
check-compose-isolation:
	@if [ "$${BRAIN_FORCE:-0}" = "1" ]; then exit 0; fi; \
	if ! docker network inspect tapps-brain_default >/dev/null 2>&1; then exit 0; fi; \
	if docker network inspect tapps-brain_default --format '{{range .Containers}}{{.Name}} {{end}}' \
	   | grep -q 'tapps-brain-dev-db'; then \
	  echo ""; \
	  echo "ERROR: tapps-brain-dev-db is on network tapps-brain_default."; \
	  echo "       It hijacks hostname tapps-brain-db and breaks the hive MCP stack."; \
	  echo "       Fix:  docker stop tapps-brain-dev-db"; \
	  echo "             docker network disconnect tapps-brain_default tapps-brain-dev-db 2>/dev/null || true"; \
	  echo "             make brain-down && make brain-up"; \
	  echo "       (brain-up uses compose project tapps-brain-dev — see docs/guides/postgres-dsn.md)"; \
	  echo "       Override: BRAIN_FORCE=1 make brain-up"; \
	  echo ""; \
	  exit 1; \
	fi

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Docker / Postgres lifecycle
# ---------------------------------------------------------------------------

brain-up:  ## Start dev Postgres (project tapps-brain-dev; safe alongside hive-up)
	@$(MAKE) check-compose-isolation
	$(DEV_COMPOSE) up -d
	@echo "Waiting for Postgres to be ready…"
	@$(DEV_COMPOSE) exec tapps-brain-db sh -c \
	  'for i in $$(seq 1 30); do pg_isready -U tapps -d tapps_brain_dev && exit 0; sleep 1; done; echo "Postgres did not become ready in time"; exit 1'
	@echo "Postgres is ready. DSN: $(TAPPS_DEV_DSN)"

brain-down:  ## Stop dev containers and remove volumes (destructive)
	$(DEV_COMPOSE) down -v

brain-restart:  ## Restart the dev Postgres container (keeps volumes)
	$(DEV_COMPOSE) restart tapps-brain-db

brain-psql:  ## Open a psql shell in the running dev Postgres container
	$(DEV_COMPOSE) exec tapps-brain-db psql -U tapps -d tapps_brain_dev

check-dev-dsn:  ## Fail loudly if TAPPS_DEV_PORT does not match the running dev DB
	@published="$$($(DEV_COMPOSE) port tapps-brain-db 5432 2>/dev/null | sed 's/.*://')"; \
	if [ -z "$$published" ]; then \
	  echo "ERROR: the tapps-brain dev Postgres is not running."; \
	  echo "  Start it first:  TAPPS_DEV_PORT=$(TAPPS_DEV_PORT) make brain-up"; \
	  exit 1; \
	fi; \
	if [ "$$published" != "$(TAPPS_DEV_PORT)" ]; then \
	  echo "ERROR: TAPPS_DEV_PORT=$(TAPPS_DEV_PORT), but the dev DB publishes $$published."; \
	  echo "  $(TAPPS_DEV_DSN)"; \
	  echo "  would silently target whatever else listens on $(TAPPS_DEV_PORT)."; \
	  echo "  Re-run with:  export TAPPS_DEV_PORT=$$published"; \
	  exit 1; \
	fi

brain-roles:  ## Provision tapps_runtime/readonly + the dev password (needed by RLS tests)
	@$(MAKE) check-dev-dsn
	@echo "Applying roles/001_db_roles.sql…"
	@$(DEV_COMPOSE) exec -T tapps-brain-db \
	  psql -U tapps -d tapps_brain_dev -v ON_ERROR_STOP=1 -q \
	  < src/tapps_brain/migrations/roles/001_db_roles.sql > /dev/null
	@$(DEV_COMPOSE) exec -T tapps-brain-db \
	  psql -U tapps -d tapps_brain_dev -v ON_ERROR_STOP=1 -q \
	  -c "ALTER ROLE tapps_runtime WITH LOGIN PASSWORD '$(TAPPS_DEV_RUNTIME_PASSWORD)';" > /dev/null
	@echo "tapps_runtime provisioned. RLS/tenant integration tests can now connect."

brain-migrate:  ## Apply schema migrations (private, hive, federation) + dev roles
	@$(MAKE) check-dev-dsn
	TAPPS_BRAIN_DATABASE_URL=$(TAPPS_DEV_DSN) \
	  uv run python scripts/apply_all_migrations.py
	@$(MAKE) brain-roles

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

# Both DSN vars are required: the requires_postgres skip gate in
# tests/conftest.py keys off TAPPS_BRAIN_DATABASE_URL, while some
# integration fixtures read TAPPS_TEST_POSTGRES_DSN. Setting only the
# latter silently skipped every requires_postgres test.
#
# TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1 mirrors .github/workflows/ci.yml: the dev
# DSN connects as the `tapps` owner (SUPERUSER + BYPASSRLS) because the suite
# applies schema migrations, and the startup guard refuses that role without
# the override.  Omitting it here made `make brain-test` fail ~200 tests with
# "refuses to start as a privileged Postgres role" that CI never sees — local
# and CI must run the same way or local runs are unusable for triage.
# Tests that assert the guard's *raising* behaviour unset it themselves (see
# tests/integration/test_rls_force_owner_guard.py).
TEST_ENV = TAPPS_TEST_POSTGRES_DSN=$(TAPPS_DEV_DSN) \
           TAPPS_BRAIN_DATABASE_URL=$(TAPPS_DEV_DSN) \
           TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1

brain-test:  ## Full test suite with coverage (requires brain-up + brain-migrate, or external DSN)
	$(TEST_ENV) \
	  $(PYTEST) tests/ -v --tb=short \
	    -m "not benchmark" \
	    --cov=tapps_brain \
	    --cov-report=term-missing \
	    --cov-fail-under=95

brain-test-fast:  ## Tests excluding slow/benchmark, no coverage, parallel (rapid iteration)
	$(TEST_ENV) \
	  $(PYTEST) tests/ --tb=short -q -m "not benchmark and not slow" -x $(PYTEST_XDIST_FLAG)

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

hive-wheel:  ## Build wheel only (dist/*.whl) — used by reload targets
	rm -f dist/*.whl dist/*.tar.gz
	uv build

hive-build: hive-wheel  ## Build wheel + Docker images for the unified tapps-brain stack
	$(HIVE_COMPOSE) build

hive-reload-http:  ## Rebuild wheel + http image only; restart brain (keep DB + visual)
	$(MAKE) check-brain-env
	@$(MAKE) check-compose-isolation
	$(MAKE) hive-wheel
	$(HIVE_COMPOSE) build tapps-brain-http
	$(HIVE_COMPOSE) up -d tapps-brain-db
	$(HIVE_COMPOSE) up -d --no-deps --force-recreate tapps-brain-http

hive-reload:  ## Rebuild wheel + http + migrate; run migrate sidecar; restart brain
	$(MAKE) check-brain-env
	@$(MAKE) check-compose-isolation
	$(MAKE) hive-wheel
	$(HIVE_COMPOSE) build tapps-brain-http tapps-brain-migrate
	$(HIVE_COMPOSE) up -d tapps-brain-db
	$(HIVE_COMPOSE) run --rm tapps-brain-migrate
	@bash scripts/migrations-changed.sh --stamp
	$(HIVE_COMPOSE) up -d --no-deps --force-recreate tapps-brain-http

dev-deploy:  ## Fast loop: reload http (or migrate if SQL changed) + brain-smoke-live
	@bash scripts/dev-deploy.sh

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
	@if ! grep -qE '^TAPPS_BRAIN_ALLOWED_ORIGINS=.+' docker/.env; then \
	  echo ""; \
	  echo "ERROR: TAPPS_BRAIN_ALLOWED_ORIGINS is missing or empty in docker/.env."; \
	  echo "       docker-compose.hive.yaml sets TAPPS_BRAIN_STRICT=1 — the brain will"; \
	  echo "       crash-loop without allowed origins. Add (local dev example):"; \
	  echo "         TAPPS_BRAIN_ALLOWED_ORIGINS=http://127.0.0.1:8088,http://localhost:8088"; \
	  echo ""; \
	  exit 1; \
	fi
	@if ! grep -qE '^TAPPS_BRAIN_INTEGRITY_KEY=.+' docker/.env; then \
	  echo ""; \
	  echo "ERROR: TAPPS_BRAIN_INTEGRITY_KEY is missing or empty in docker/.env."; \
	  echo "       Without it, integrity HMAC is disabled and verify_integrity is a no-op."; \
	  echo "       Generate and set (example):"; \
	  echo "         openssl rand -base64 32   # → TAPPS_BRAIN_INTEGRITY_KEY=..."; \
	  echo ""; \
	  exit 1; \
	fi
	@env_ver=$$(grep -E '^BRAIN_VERSION=' docker/.env | head -1 | cut -d= -f2- | tr -d '[:space:]'); \
	py_ver=$$(grep -E '^version[[:space:]]*=' pyproject.toml | head -1 | sed 's/.*=[[:space:]]*"\(.*\)"/\1/'); \
	if [ -z "$$env_ver" ]; then \
	  echo ""; \
	  echo "ERROR: BRAIN_VERSION is missing from docker/.env."; \
	  echo "       Set it to match pyproject.toml (currently $$py_ver):"; \
	  echo "         BRAIN_VERSION=$$py_ver"; \
	  echo ""; \
	  exit 1; \
	fi; \
	if [ "$$env_ver" != "$$py_ver" ]; then \
	  echo ""; \
	  echo "ERROR: BRAIN_VERSION drift — docker/.env has '$$env_ver' but pyproject.toml has '$$py_ver'."; \
	  echo "       Align the pin so compose rolls http + visual + migrate together:"; \
	  echo "         sed -i 's/^BRAIN_VERSION=.*/BRAIN_VERSION=$$py_ver/' docker/.env"; \
	  echo "       Also keep docker/.env.example in sync on version bumps."; \
	  echo ""; \
	  exit 1; \
	fi

TIER ?= balanced

brain-env-init:  ## Append docker/defaults/$(TIER).env to docker/.env (replaces prior tier block)
	@tier="$(TIER)"; \
	tier_file="docker/defaults/$${tier}.env"; \
	if [ ! -f "$$tier_file" ]; then \
	  echo ""; \
	  echo "ERROR: unknown tier '$$tier' (missing $$tier_file)"; \
	  echo "       Valid tiers: cheap balanced quality it13"; \
	  echo ""; \
	  exit 1; \
	fi; \
	if [ ! -f docker/.env ]; then \
	  cp docker/.env.example docker/.env; \
	  echo "Created docker/.env from docker/.env.example — fill REPLACE_ME_* before deploy."; \
	fi; \
	tmp=$$(mktemp); \
	awk 'BEGIN{skip=0} /^TAPPS_BRAIN_TIER=/{skip=1; next} skip && /^TAPPS_BRAIN_DEFAULT_PROFILE=/{skip=0; next} skip{next} {print}' docker/.env > "$$tmp"; \
	mv "$$tmp" docker/.env; \
	printf '\n' >> docker/.env; \
	cat "$$tier_file" >> docker/.env; \
	echo "Appended tier overlay: $$tier_file"

hive-deploy:  ## Full deploy: check env → build → migrate → up. Safe to rerun.
	$(MAKE) check-brain-env
	@$(MAKE) check-compose-isolation
	$(MAKE) hive-build
	$(HIVE_COMPOSE) up -d

hive-up:  ## Start the unified brain stack without rebuilding
	$(MAKE) check-brain-env
	@$(MAKE) check-compose-isolation
	$(HIVE_COMPOSE) up -d

hive-down:  ## Stop brain containers (keeps volumes — data preserved)
	$(HIVE_COMPOSE) down

hive-logs:  ## Tail logs from running brain services
	$(HIVE_COMPOSE) logs -f

hive-smoke:  ## End-to-end stack smoke test (boots full stack, asserts endpoints, tears down)
	@bash scripts/hive_smoke.sh

brain-healthcheck:  ## Live MCP round-trip (server-mode OK for bridge-only); not the deploy smoke gate
	@bash scripts/brain-healthcheck.sh

brain-smoke-live:  ## Canonical post-deploy HTTP smoke (prefer this after hive/dev-deploy)
	@bash scripts/brain_smoke_live.sh

brain-diagnostics-live:  ## Live-stack diagnostics (healthz, snapshot, stale, report); AUTO_GC=1 archives stale
	@bash scripts/brain_diagnostics_live.sh

brain-eval:  ## Operational eval over a window (WINDOW_HOURS=72 default): metrics, usage, logs, recommendations
	@python3 scripts/brain_eval.py

purge-test-tenants:  ## Remove leaked test/load tenant rows (reserved smoke-/test- prefixes). Dry-run unless APPLY=1. Override: PURGE_DSN=postgres://… make purge-test-tenants
	@TAPPS_BRAIN_DATABASE_URL=$(or $(PURGE_DSN),$(TAPPS_DEV_DSN)) \
	  TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1 \
	  uv run tapps-brain maintenance purge-test-tenants $(if $(filter 1,$(APPLY)),--apply,)

brain-visual-smoke-live:  ## Visual dashboard smoke (:8088 HTML + /snapshot proxy + direct :8080/snapshot)
	@bash scripts/brain_visual_smoke_live.sh

publish-brain-image:  ## Build wheel + all three stack images, each tagged :latest and :$(BRAIN_VERSION) (TAP-2136)
	rm -f dist/*.whl dist/*.tar.gz
	uv build
	docker build \
	  --build-arg TAPPS_BRAIN_VERSION=$(BRAIN_VERSION) \
	  -f docker/Dockerfile.http \
	  -t $(BRAIN_IMAGE):latest \
	  -t $(BRAIN_IMAGE):$(BRAIN_VERSION) \
	  .
	docker build \
	  --build-arg TAPPS_BRAIN_VERSION=$(BRAIN_VERSION) \
	  -f docker/Dockerfile.migrate \
	  -t docker-tapps-brain-migrate:latest \
	  -t docker-tapps-brain-migrate:$(BRAIN_VERSION) \
	  .
	docker build \
	  --build-arg TAPPS_BRAIN_VERSION=$(BRAIN_VERSION) \
	  -f docker/Dockerfile.visual \
	  -t docker-tapps-visual:latest \
	  -t docker-tapps-visual:$(BRAIN_VERSION) \
	  .
