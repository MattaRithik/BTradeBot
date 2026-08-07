VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: setup install install-dev test lint typecheck check doctor demo clean

setup:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev,dashboard,excel]"

install-dev:
	$(PIP) install -q -e ".[dev,dashboard,excel]"

test:
	$(PY) -m pytest

lint:
	$(VENV)/bin/ruff check src tests

format:
	$(VENV)/bin/ruff format src tests

typecheck:
	$(VENV)/bin/mypy

check: lint typecheck test

doctor:
	$(PY) -m quant_platform.cli.main doctor

demo:
	$(PY) -m quant_platform.cli.main demo

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
