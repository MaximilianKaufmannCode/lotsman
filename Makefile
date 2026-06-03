# Лоцман — canonical task runner
# All targets are PHONY. Run `make help` to list available commands.
#
# Prerequisites:
#   - Docker 26+ with Compose v2 (docker compose, not docker-compose)
#   - uv  (https://github.com/astral-sh/uv)  — Python package manager
#   - pnpm (https://pnpm.io)                 — Node package manager
#
# Quick start: copy .env.example to .env, fill in required values, then: make dev

COMPOSE_DEV  := docker compose -f infra/compose.dev.yml
COMPOSE_PROD := docker compose -f infra/compose.prod.yml
COMPOSE_OBS  := $(COMPOSE_DEV) -f infra/compose.observability.yml

.DEFAULT_GOAL := help

.PHONY: help dev up down obs-up obs-down migrate seed test lint typecheck fmt \
        ci-local build clean db-shell redis-cli logs admin-create superadmin-create \
        system-up

# ── Help ───────────────────────────────────────────────────────────────────────

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' \
		| sort

# ── Development ────────────────────────────────────────────────────────────────

dev: ## Start full dev stack with hot reload, then run migrate + seed
	$(COMPOSE_DEV) up -d --build
	$(MAKE) migrate
	$(MAKE) seed

up: ## Start prod-like stack (requires LOTSMAN_VERSION to be set)
	$(COMPOSE_PROD) up -d

down: ## Stop all running stacks (dev + observability)
	$(COMPOSE_OBS) down 2>/dev/null || $(COMPOSE_DEV) down

obs-up: ## Bring up the observability overlay (Prometheus, Grafana, Loki, Promtail)
	$(COMPOSE_OBS) up -d

obs-down: ## Tear down the observability overlay
	$(COMPOSE_OBS) down

logs: ## Tail logs from all dev services
	$(COMPOSE_DEV) logs -f --tail=100

# ── Database ───────────────────────────────────────────────────────────────────

migrate: ## Apply Alembic migrations for all four services
	@echo "==> Running migrations..."
	@for svc in auth registry notification audit; do \
		echo "  -- $${svc}-svc: alembic upgrade head"; \
		$(COMPOSE_DEV) run --rm $${svc}-svc alembic upgrade head; \
	done
	@echo "==> Migrations complete."

seed: ## Insert minimal demo data (registry-svc seed script)
	@echo "==> Seeding demo data..."
	@$(COMPOSE_DEV) run --rm registry-svc python -m registry_service.scripts.seed
	@echo "==> Seed complete."

db-shell: ## Open a psql shell in the running Postgres container
	$(COMPOSE_DEV) exec postgres psql -U postgres lotsman

redis-cli: ## Open a redis-cli shell in the running Redis container
	$(COMPOSE_DEV) exec redis redis-cli

# ── Quality gates ─────────────────────────────────────────────────────────────

lint: ## Lint Python (ruff) and JS/TS (biome) — skips if no code exists yet
	@if find services -name "pyproject.toml" -print -quit 2>/dev/null | grep -q .; then \
		echo "==> ruff lint"; \
		uv run ruff check .; \
	else \
		echo "==> [lint] No Python packages found in services/ — skipping ruff"; \
	fi
	@if [ -f web/package.json ]; then \
		echo "==> biome lint"; \
		pnpm -C web biome check; \
	else \
		echo "==> [lint] No web/package.json found — skipping biome"; \
	fi

typecheck: ## Type-check Python (mypy --strict) and JS/TS (tsc) — skips if no code exists yet
	@if find services -name "pyproject.toml" -print -quit 2>/dev/null | grep -q .; then \
		echo "==> mypy"; \
		uv run mypy --strict services/; \
	else \
		echo "==> [typecheck] No Python packages found in services/ — skipping mypy"; \
	fi
	@if [ -f web/tsconfig.json ]; then \
		echo "==> tsc"; \
		pnpm -C web tsc --noEmit; \
	else \
		echo "==> [typecheck] No web/tsconfig.json found — skipping tsc"; \
	fi

test: ## Run all tests (pytest + vitest) — skips if no code exists yet
	@if find services -name "pyproject.toml" -print -quit 2>/dev/null | grep -q .; then \
		echo "==> pytest"; \
		uv run pytest -q; \
	else \
		echo "==> [test] No Python packages found in services/ — skipping pytest"; \
	fi
	@if [ -f web/package.json ]; then \
		echo "==> vitest"; \
		pnpm -C web test --run; \
	else \
		echo "==> [test] No web/package.json found — skipping vitest"; \
	fi

fmt: ## Auto-format Python (ruff format) and JS/TS (biome format)
	@if find services -name "pyproject.toml" -print -quit 2>/dev/null | grep -q .; then \
		uv run ruff format .; \
	fi
	@if [ -f web/package.json ]; then \
		pnpm -C web biome format --write; \
	fi

ci-local: ## Run everything CI runs (lint + typecheck + test) — local validation gate
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test

# ── Administration ─────────────────────────────────────────────────────────────

admin-create: ## Bootstrap a Space-admin user (EMAIL=... FULL_NAME="...")
	@if [ -z "$(EMAIL)" ] || [ -z "$(FULL_NAME)" ]; then \
		echo "Usage: make admin-create EMAIL=admin@org.local FULL_NAME=\"Иван Петров\""; exit 1; \
	fi
	$(COMPOSE_DEV) exec -T auth-svc python -m auth_service.scripts.bootstrap_admin \
		--email "$(EMAIL)" --full-name "$(FULL_NAME)"

superadmin-create: ## Bootstrap a Super-admin user (EMAIL=... FULL_NAME="...")
	@if [ -z "$(EMAIL)" ] || [ -z "$(FULL_NAME)" ]; then \
		echo "Usage: make superadmin-create EMAIL=super@org.local FULL_NAME=\"Super Admin\""; exit 1; \
	fi
	$(COMPOSE_DEV) exec -T auth-svc python -m auth_service.scripts.bootstrap_super_admin \
		--email "$(EMAIL)" --full-name "$(FULL_NAME)"

system-up: ## Start the system-control sidecar only (requires full stack to be up first)
	$(COMPOSE_DEV) up -d system-control

# ── Build ──────────────────────────────────────────────────────────────────────

build: ## Build all production Docker images
	$(COMPOSE_PROD) build

# ── Cleanup ────────────────────────────────────────────────────────────────────

clean: ## Stop containers, remove volumes, delete all build caches
	@echo "==> Stopping and removing containers + volumes..."
	$(COMPOSE_OBS) down -v 2>/dev/null || $(COMPOSE_DEV) down -v
	@echo "==> Removing Python caches..."
	@find . -type d -name "__pycache__"  -not -path '*/.git/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -not -path '*/.git/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache"   -not -path '*/.git/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache"   -not -path '*/.git/*' -exec rm -rf {} + 2>/dev/null || true
	@echo "==> Removing Node caches..."
	@find . -type d -name "node_modules" -not -path '*/.git/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name "dist"          -not -path '*/.git/*' -exec rm -rf {} + 2>/dev/null || true
	@echo "==> Clean complete."
