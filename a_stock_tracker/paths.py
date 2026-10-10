"""One project; private research state and calendar evidence have separate paths."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path(os.getenv("TRACKER_CONFIG_DIR", PROJECT_ROOT / "config")).expanduser().resolve()
DATA_DIR = Path(os.getenv("TRACKER_DATA_DIR", PROJECT_ROOT / "data")).expanduser().resolve()
CALENDAR_PATH = DATA_DIR / "calendar" / "trading_calendar.json"
