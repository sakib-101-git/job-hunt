# Developer entry points. `make check` runs everything CI runs, except gitleaks.
.DEFAULT_GOAL := help
.PHONY: help setup hooks lint format typecheck test guard bot-dev check

UV := uv --directory pipeline
NPM := npm --prefix bot

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup: hooks ## Install Python and Node dependencies and the git hooks
	$(UV) sync --locked
	$(NPM) ci

hooks: ## Point git at the repository hooks in .githooks/
	git config core.hooksPath .githooks

lint: ## Ruff and ESLint
	$(UV) run ruff check ..
	$(UV) run ruff format --check ..
	$(NPM) run lint

format: ## Apply Ruff formatting and safe fixes
	$(UV) run ruff format ..
	$(UV) run ruff check --fix ..

typecheck: ## mypy (strict) and tsc
	$(UV) run mypy
	$(NPM) run typecheck

test: ## pytest with coverage and vitest
	$(UV) run pytest --cov
	$(NPM) test

guard: ## Repository rules: tracked files and the last commit message
	python3 scripts/repo_guard.py tracked-files
	git log -1 --format=%B | python3 scripts/repo_guard.py attribution

bot-dev: ## Run the Worker locally with wrangler dev
	$(NPM) run dev

check: lint typecheck test guard ## Everything CI checks
