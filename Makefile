.PHONY: setup demo check test-mobile
PYTHON := .venv/bin/python

setup:
	uv sync --frozen --all-extras

demo:
	APP_MODE=demo PORT=8551 PUBLIC_BASE_URL=http://127.0.0.1:8551 $(PYTHON) -m a_stock_tracker.web

check:
	$(PYTHON) -m pytest tests -o addopts='' -q
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy
	git diff --check

test-mobile:
	$(PYTHON) -m tests.test_mobile
