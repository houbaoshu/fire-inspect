.PHONY: dev api worker test lint migrate seed compose

dev:
	cd frontend && npm run dev

api:
	uv run uvicorn backend.app.main:app --reload --port 8000

worker:
	uv run celery -A backend.app.tasks.celery_app worker --loglevel=INFO

test:
	uv run pytest
	cd frontend && npm test -- --run

lint:
	uv run ruff check .
	cd frontend && npm run lint

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m backend.app.cli create-admin

compose:
	docker compose up --build

