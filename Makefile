.PHONY: setup dev test lint down migrate acceptance logs docker-setup docker-dev docker-down docker-test docker-acceptance

PY ?= $(firstword $(wildcard .venv/Scripts/python.exe .venv/bin/python) python)
PIP = $(PY) -m pip
BACKEND = backend
export PYTHONPATH := $(BACKEND)

setup:
	@test -f .env || cp .env.example .env
	@test -d .venv || python -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(BACKEND)[dev]"
	cd dashboard && npm install

dev:
	@echo "On Windows use: ./scripts/dev.ps1"
	@echo "Starting API on :8000. In other terminals start worker, scheduler, and dashboard."
	$(PY) -m uvicorn ame.api.main:app --host 127.0.0.1 --port 8000

test:
	cd $(BACKEND) && ../$(PY) -m pytest -q

lint:
	cd $(BACKEND) && ../$(PY) -m ruff check ame tests
	cd $(BACKEND) && ../$(PY) -m ruff format --check ame tests

acceptance:
	$(PY) -m ame.cli.acceptance

migrate:
	$(PY) -m alembic -c $(BACKEND)/alembic.ini upgrade head

down:
	@echo "On Windows use: ./scripts/stop.ps1"
	@echo "No Docker processes to stop. Native PIDs live in .ame/pids.json."

logs:
	@echo "Native logs: .ame/logs/"

# Optional deployment tooling. Not required for V1 development or acceptance.
docker-setup:
	cp -n .env.example .env || true
	docker compose pull
	docker compose build
	docker compose run --rm api alembic upgrade head

docker-dev:
	docker compose up --build

docker-down:
	docker compose down

docker-test:
	docker compose run --rm api pytest -q

docker-acceptance:
	docker compose run --rm api python -m ame.cli.acceptance
