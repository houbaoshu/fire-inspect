UV_CACHE_DIR ?= $(CURDIR)/.uv-cache
PYTEST = UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen pytest
LOCAL_ACCEPTANCE_ENV =
ifeq ($(shell uname -s),Darwin)
LOCAL_ACCEPTANCE_ENV = TMPDIR=/private/tmp
endif

.PHONY: sync fast backend frontend local-acceptance real-ai windows-docx postgres browser-e2e acceptance-coverage dev-ai

dev-ai:
	./scripts/dev_with_ai.sh

sync:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync --frozen --group dev
	pnpm --dir frontend install --frozen-lockfile

backend:
	mkdir -p test-results
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/verify_authoritative_assets.py
	$(PYTEST) -m "not local_acceptance and not real_ai and not windows_docx and not postgres" --junitxml=test-results/backend-fast.xml
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen ruff check .

frontend:
	mkdir -p test-results
	pnpm --dir frontend exec vitest run --reporter=default --reporter=junit --outputFile.junit=../test-results/frontend-fast.xml
	pnpm --dir frontend typecheck
	pnpm --dir frontend typecheck:e2e
	pnpm --dir frontend build

acceptance-coverage:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/validate_acceptance_coverage.py

fast: backend frontend
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/validate_acceptance_coverage.py --require-gate fast

local-acceptance:
	mkdir -p test-results
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/verify_authoritative_assets.py --include-sensitive
	$(LOCAL_ACCEPTANCE_ENV) $(PYTEST) --run-local-acceptance -m local_acceptance --junitxml=test-results/local-acceptance.xml
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/validate_acceptance_coverage.py --require-gate local-acceptance

real-ai:
	mkdir -p test-results
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/check_external_gate.py real-ai
	$(PYTEST) --run-real-ai -m real_ai --junitxml=test-results/real-ai.xml
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/validate_acceptance_coverage.py --require-gate real-ai

windows-docx:
	mkdir -p test-results
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/check_external_gate.py windows-docx
	$(PYTEST) --run-windows-docx -m windows_docx --junitxml=test-results/windows-docx.xml
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/validate_acceptance_coverage.py --require-gate windows-docx

postgres:
	mkdir -p test-results
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/check_external_gate.py postgres
	@FIRE_INSPECT_DATABASE_URL="$$FIRE_INSPECT_POSTGRES_TEST_URL" UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen pytest --run-postgres -m postgres --junitxml=test-results/postgres.xml
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/validate_acceptance_coverage.py --require-gate postgres

browser-e2e:
	mkdir -p test-results
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/check_external_gate.py browser-e2e
	pnpm --dir frontend test:e2e
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --frozen python scripts/validate_acceptance_coverage.py --require-gate browser-e2e
