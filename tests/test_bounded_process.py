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


@pytest.mark.parametrize("explicit_env", [False, True])
def test_only_identified_lease_is_inherited_and_marker_is_removed(
    tmp_path, monkeypatch, explicit_env
):
    import os

    from a_stock_tracker.bounded_process import EXECUTION_LEASE_ENV, execution_lease_environment

    with (tmp_path / "lease").open("w") as lease, (tmp_path / "unrelated").open("w") as other:
        marker = execution_lease_environment(lease.fileno())[EXECUTION_LEASE_ENV]
        monkeypatch.setenv(EXECUTION_LEASE_ENV, marker)
        monkeypatch.setenv("SYNTHETIC", "yes")
        os.set_inheritable(other.fileno(), True)
        script = """
import os, sys
fd, device, inode = map(int, sys.argv[1].split(':'))
info = os.fstat(fd)
assert (info.st_dev, info.st_ino) == (device, inode)
assert sys.argv[3] not in os.environ
try:
    os.fstat(int(sys.argv[2]))
except OSError:
    pass
else:
    raise AssertionError('unrelated descriptor inherited')
assert os.environ['SYNTHETIC'] == 'yes'
"""
        # Model calls supply a sanitized environment that omits receiver variables.
        result = run_bounded(
            [sys.executable, "-c", script, marker, str(other.fileno()), EXECUTION_LEASE_ENV],
            timeout=3,
            check=True,
            env={"SYNTHETIC": "yes"} if explicit_env else None,
        )
        assert result.returncode == 0


@pytest.mark.parametrize("marker", ["", "no-fd", "1:2:3", "999999:2:3", "4:2:3:4"])
def test_invalid_execution_lease_refused_before_launch(marker, monkeypatch):
    from a_stock_tracker.bounded_process import EXECUTION_LEASE_ENV

    monkeypatch.setenv(EXECUTION_LEASE_ENV, marker)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("started with bad lease"))
    with pytest.raises(ScreenError, match="inherited execution lease"):
        run_bounded(["synthetic"], timeout=1)


def test_reused_execution_lease_descriptor_refused(tmp_path, monkeypatch):
    from a_stock_tracker.bounded_process import EXECUTION_LEASE_ENV, execution_lease_environment

    with (tmp_path / "lease").open("w") as lease:
        marker = execution_lease_environment(lease.fileno())[EXECUTION_LEASE_ENV]
    with (tmp_path / "unrelated").open("w"):
        monkeypatch.setenv(EXECUTION_LEASE_ENV, marker)
        monkeypatch.setattr(
            subprocess, "Popen", lambda *a, **kw: pytest.fail("started with stale lease")
        )
        with pytest.raises(ScreenError, match="inherited execution lease"):
            run_bounded(["synthetic"], timeout=1)
