PYTHON ?= python

.PHONY: dev test lint format migrate import-companies poll

dev:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy app

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

migrate:
	$(PYTHON) -m alembic upgrade head

import-companies:
	$(PYTHON) -m app.cli import-companies

poll:
	$(PYTHON) -m app.cli poll
