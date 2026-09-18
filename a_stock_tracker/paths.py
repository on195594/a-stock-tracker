"""Central project paths shared by application modules and command-line tools."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
QUALITATIVE_CONFIG_DIR = CONFIG_DIR / "qualitative"
WEIGHTS_PATH = CONFIG_DIR / "weights.json"
EXPERIMENT_MANIFEST_PATH = CONFIG_DIR / "experiment_manifest.json"


def experiment_manifest_path() -> Path:
    """Resolve the configured project manifest at call time."""
    return PROJECT_ROOT / "config" / "experiment_manifest.json"


def trading_calendar_path() -> Path:
    """Resolve the configured read-only local calendar evidence at call time."""
    return PROJECT_ROOT / "config" / "trading_calendar.json"


ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
REPORTS_DIR = ARTIFACTS_DIR / "reports"
ACCURACY_REPORT_PATH = REPORTS_DIR / "accuracy-report.txt"
CREDENTIALS_DIR = PROJECT_ROOT / "credentials"
LOG_DIR = PROJECT_ROOT / "logs"
