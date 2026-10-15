"""Run the Web with uvicorn; configuration comes only from the environment."""

import uvicorn

from a_stock_tracker.web.settings import settings_from_env


def main() -> None:
    settings = settings_from_env()
    uvicorn.run(
        "a_stock_tracker.web.routes:create_app_from_env",
        factory=True,
        host=settings.host,
        port=settings.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
