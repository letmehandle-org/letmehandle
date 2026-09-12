# The single entry point. Every gate a contributor runs and every gate CI runs is a target
# here, so the two cannot drift: CI calls these, it does not reimplement them.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

BACKEND := apps/backend
MOBILE  := apps/mobile

define CHECK_DATABASE
import asyncio, os, sys
import asyncpg
url = os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "")
async def main():
    connection = await asyncpg.connect(url, timeout=3)
    await connection.close()
asyncio.run(main())
endef
export CHECK_DATABASE

# Where the local database is. Overridable, because 5432 is a popular port and a contributor
# may already have something on it: `POSTGRES_PORT=5433 make verify` moves both the stack and
# the tests together.
POSTGRES_PORT ?= 5432
BACKEND_PORT  ?= 8000
export POSTGRES_PORT
export BACKEND_PORT

# The tests that need a database read this. Without it they skip, and the coverage floor is
# then unreachable — which reads as a failing build rather than as a missing database, so it is
# set here rather than left to each developer's shell.
export TEST_DATABASE_URL ?= postgresql+asyncpg://letmehandle:letmehandle@127.0.0.1:$(POSTGRES_PORT)/letmehandle

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: hooks
hooks: ## Install the git hooks (they are not cloned; this is the only way they run)
	@git config core.hooksPath .githooks
	@echo "hooks installed: $$(ls .githooks | tr '\n' ' ')"

.PHONY: setup
setup: hooks ## Set up everything a fresh clone needs
	@if [ -d $(BACKEND) ]; then cd $(BACKEND) && uv sync --all-extras; fi
	@if [ -d $(MOBILE) ]; then pnpm install; fi
	@echo "setup complete"

.PHONY: api-types
api-types: ## Regenerate the OpenAPI schema and the mobile app's types from it
	@cd $(BACKEND) && uv run python ../../scripts/generate_openapi.py
	@pnpm --filter @letmehandle/api-client generate

.PHONY: api-types-check
api-types-check: ## Fail if the generated types have drifted from the backend
	@$(MAKE) --no-print-directory api-types
	@if ! git diff --quiet -- packages/api-client; then \
		echo ""; \
		echo "The generated API types are out of date."; \
		echo "The backend's request or response models changed and this was not regenerated,"; \
		echo "so the mobile app is compiling against a contract the backend no longer has."; \
		echo ""; \
		git --no-pager diff --stat -- packages/api-client; \
		echo ""; \
		echo "Run: make api-types   and commit the result."; \
		exit 1; \
	fi

.PHONY: verify
verify: audit lint typecheck test coverage api-types-check ## Everything. What pre-push and CI run.
	@echo -e "\033[32mverify passed\033[0m"

.PHONY: audit
audit: ## Check that nothing private reached a tracked file
	@python3 scripts/disclosure_audit.py
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks detect --redact --no-banner --config .gitleaks.toml; \
	else \
		echo "gitleaks: not installed, skipped locally (CI runs it)"; \
	fi

.PHONY: lint
lint: ## Lint and check formatting
	@if [ -d $(BACKEND) ]; then cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .; fi
	@if [ -d $(MOBILE) ]; then pnpm --filter mobile lint; fi

.PHONY: format
format: ## Apply formatting
	@if [ -d $(BACKEND) ]; then cd $(BACKEND) && uv run ruff format . && uv run ruff check --fix .; fi
	@if [ -d $(MOBILE) ]; then pnpm --filter mobile format; fi

.PHONY: typecheck
typecheck: ## Type check both applications, and the import boundaries
	@if [ -d $(BACKEND) ]; then cd $(BACKEND) && uv run mypy src tests && uv run lint-imports; fi
	@if [ -d $(MOBILE) ]; then pnpm --filter mobile typecheck; fi
	@if [ -d packages/api-client ]; then pnpm --filter @letmehandle/api-client typecheck; fi

.PHONY: test
test: ## Run the unit and integration suites
	@if [ -d $(BACKEND) ]; then cd $(BACKEND) && uv run pytest; fi
	@if [ -d $(MOBILE) ]; then pnpm --filter mobile test; fi

.PHONY: coverage
coverage: database-or-explain ## Enforce the coverage floors from D-020
	@if [ -d $(BACKEND) ]; then cd $(BACKEND) && uv run pytest --cov --cov-report=term-missing --cov-fail-under=98; fi
	@if [ -d $(MOBILE) ]; then pnpm --filter mobile test:coverage; fi

.PHONY: database-or-explain
database-or-explain:
	@# A connection, not a port check: something else answering on 5432 is the common case on a
	@# machine that runs more than one project, and a port that opens tells you nothing about
	@# whether this project's database is behind it.
	@cd $(BACKEND) && uv run python -c "$$CHECK_DATABASE" 2>/dev/null || { \
		echo ""; \
		echo "No database on port $(POSTGRES_PORT)."; \
		echo ""; \
		echo "The tests that talk to one will skip, and the coverage floor is then out of"; \
		echo "reach — which looks like a failing build rather than a missing database."; \
		echo ""; \
		echo "  make up                              start it"; \
		echo "  POSTGRES_PORT=5433 make up verify    if 5432 is already taken"; \
		echo ""; \
		exit 1; \
	}

.PHONY: up
up: ## Start the local dependencies and the backend
	@docker compose up -d --wait
	@echo "backend: http://localhost:8000/health"

.PHONY: down
down: ## Stop them
	@docker compose down

.PHONY: clean
clean: ## Remove build and cache output
	@find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \
		-o -name .mypy_cache \) -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf coverage htmlcov .coverage
