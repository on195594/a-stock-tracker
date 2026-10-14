"""Project paths used to exclude historical research directories from replay."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("TRACKER_DATA_DIR", PROJECT_ROOT / "data")).expanduser().resolve()
