# Developer entry points. `make check` runs everything CI runs, except gitleaks.
.DEFAULT_GOAL := help
.PHONY: help setup hooks lint format typecheck test config build-shared migrate-local guard bot-dev check

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

config: ## Validate config/ and check the Worker's generated copy is current
	$(UV) run jobhunt validate-config
	$(UV) run jobhunt build-shared --check

build-shared: ## Regenerate bot/src/generated/ from config/
	$(UV) run jobhunt build-shared

migrate-local: ## Apply migrations/ to the local D1 that `make bot-dev` uses
	cd bot && npx wrangler d1 migrations apply jobhunt --local

guard: ## Repository rules: tracked files and the last commit message
	python3 scripts/repo_guard.py tracked-files
	git log -1 --format=%B | python3 scripts/repo_guard.py attribution

bot-dev: ## Run the Worker locally with wrangler dev
	$(NPM) run dev

check: lint typecheck test config guard ## Everything CI checks
