"""Central project paths shared by application modules and command-line tools."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
QUALITATIVE_CONFIG_DIR = CONFIG_DIR / "qualitative"
WEIGHTS_PATH = CONFIG_DIR / "weights.json"
EXPERIMENT_MANIFEST_PATH = CONFIG_DIR / "experiment_manifest.json"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
REPORTS_DIR = ARTIFACTS_DIR / "reports"
ACCURACY_REPORT_PATH = REPORTS_DIR / "accuracy-report.txt"
CREDENTIALS_DIR = PROJECT_ROOT / "credentials"
LOG_DIR = PROJECT_ROOT / "logs"
