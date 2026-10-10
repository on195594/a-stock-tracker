.PHONY: setup demo check test-mobile calendar
PYTHON := .venv/bin/python

setup:
	uv sync --frozen --all-extras

demo:
	APP_MODE=demo PUBLIC_BASE_URL=http://127.0.0.1:8550 FLET_WEB_NO_CDN=true $(PYTHON) -m a_stock_tracker.app

check:
	$(PYTHON) -m pytest tests -o addopts='' -q
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy
	git diff --check

test-mobile:
	$(PYTHON) -m tests.test_mobile

calendar:
	$(PYTHON) -m a_stock_tracker.calendar
