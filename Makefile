.PHONY: setup demo check test-mobile calendar
PYTHON := .venv/bin/python

setup:
	uv sync --frozen --all-extras

demo:
	$(PYTHON) -m a_stock_tracker.manage setup-demo --state-dir .local/demo --journal-mode DELETE
	@APP_MODE=demo STATE_DIR=.local/demo $(PYTHON) -m a_stock_tracker.worker --mode demo --state-dir .local/demo & worker_pid=$$!; \
	trap 'kill $$worker_pid 2>/dev/null || true; wait $$worker_pid 2>/dev/null || true' EXIT; \
	APP_MODE=demo STATE_DIR=.local/demo PUBLIC_BASE_URL=http://127.0.0.1:8550 FLET_WEB_NO_CDN=true $(PYTHON) -m a_stock_tracker.app

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
