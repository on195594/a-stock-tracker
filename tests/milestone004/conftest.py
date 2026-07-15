from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def block_real_network_and_reviewers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every M4 test synthetic while allowing harmless Python subprocesses."""

    def blocked_connection(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("MILESTONE-004 tests may not use the network")

    real_popen = subprocess.Popen

    def guarded_popen(args: Any, *positional: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        executable = Path(str(args[0])).name.lower()
        if executable in {"codex", "agy", "gemini"}:
            raise AssertionError(f"real reviewer execution is forbidden: {executable}")
        return real_popen(args, *positional, **kwargs)

    monkeypatch.setattr(socket, "create_connection", blocked_connection)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
