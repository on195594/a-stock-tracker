import subprocess
import sys

import pytest

from a_stock_tracker.bounded_process import run_bounded
from a_stock_tracker.research import ScreenError


def test_output_limit_includes_stderr_and_stops_writer():
    with pytest.raises(ScreenError, match="output budget"):
        run_bounded(
            [sys.executable, "-c", "import os;\nwhile True: os.write(2, b'x' * 4096)"],
            timeout=3,
            max_bytes=8192,
        )


def test_deadline_includes_process_that_closed_its_pipes():
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded(
            [sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(3)"],
            timeout=0.1,
        )
    result = run_bounded([sys.executable, "-c", "print('合成')"], timeout=3, check=True)
    assert result.stdout == "合成\n" and result.returncode == 0
