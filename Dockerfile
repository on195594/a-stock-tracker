# syntax=docker/dockerfile:1
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY a_stock_tracker ./a_stock_tracker
COPY config/anchors.json config/trading_calendar.json ./config/
COPY tests/fixtures ./tests/fixtures
COPY assets ./assets
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8550
ENTRYPOINT ["/entrypoint.sh"]
