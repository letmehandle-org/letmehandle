# The single entry point. Every gate a contributor runs and every gate CI runs is a target
# here, so the two cannot drift: CI calls these, it does not reimplement them.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

BACKEND := apps/backend
MOBILE  := apps/mobile

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

.PHONY: verify
verify: audit lint typecheck test coverage ## Everything. What pre-push and CI run.
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

.PHONY: test
test: ## Run the unit and integration suites
	@if [ -d $(BACKEND) ]; then cd $(BACKEND) && uv run pytest; fi
	@if [ -d $(MOBILE) ]; then pnpm --filter mobile test; fi

.PHONY: coverage
coverage: ## Enforce the coverage floors from D-020
	@if [ -d $(BACKEND) ]; then cd $(BACKEND) && uv run pytest --cov --cov-report=term-missing --cov-fail-under=98; fi
	@if [ -d $(MOBILE) ]; then pnpm --filter mobile test:coverage; fi

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
