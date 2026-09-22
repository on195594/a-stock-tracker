"""Central project paths shared by application modules and command-line tools."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
WEIGHTS_PATH = CONFIG_DIR / "weights.json"
TRACKED_TRADING_CALENDAR_PATH = CONFIG_DIR / "trading_calendar.json"
RUNTIME_TRADING_CALENDAR_PATH = DATA_DIR / "trading_calendar.json"


def experiment_manifest_path() -> Path:
    """Resolve the configured project manifest at call time."""
    return PROJECT_ROOT / "config" / "experiment_manifest.json"


def trading_calendar_path() -> Path:
    """Prefer refreshed runtime evidence, falling back to the tracked seed."""
    runtime = PROJECT_ROOT / "data" / "trading_calendar.json"
    tracked = PROJECT_ROOT / "config" / "trading_calendar.json"
    return runtime if runtime.is_file() else tracked


ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
REPORTS_DIR = ARTIFACTS_DIR / "reports"
ACCURACY_REPORT_PATH = REPORTS_DIR / "accuracy-report.txt"
LOG_DIR = PROJECT_ROOT / "logs"
