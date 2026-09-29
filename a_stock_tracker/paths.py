"""Paths used by the active data collectors; runtime evidence stays outside Git."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
TRACKED_TRADING_CALENDAR_PATH = CONFIG_DIR / "trading_calendar.json"
RUNTIME_TRADING_CALENDAR_PATH = DATA_DIR / "trading_calendar.json"
