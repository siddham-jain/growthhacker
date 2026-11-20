TEST_DATABASE_URL ?= postgresql+asyncpg://growth:growth@localhost:5432/growthhacker_test

.PHONY: install migrate migrate-test downgrade-test revision check-migrations test test-foundation lint format check dev worker

install:
	uv sync

migrate:
	uv run alembic upgrade head

migrate-test:
	uv run alembic -x db_url=$(TEST_DATABASE_URL) upgrade head

downgrade-test:
	uv run alembic -x db_url=$(TEST_DATABASE_URL) downgrade base

revision:
	uv run alembic revision --autogenerate -m "$(m)"

check-migrations:
	uv run alembic -x db_url=$(TEST_DATABASE_URL) upgrade head
	uv run alembic -x db_url=$(TEST_DATABASE_URL) check

test:
	uv run pytest -q

test-foundation:
	uv run pytest tests/foundation -q

lint:
	uv run ruff check app tests migrations

format:
	uv run ruff format app tests migrations

check: lint check-migrations test

dev:
	uv run uvicorn app.main:app --reload --port 8000

worker:
	uv run arq app.workers.settings.InteractiveWorkerSettings
