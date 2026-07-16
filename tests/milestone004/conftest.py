from __future__ import annotations

import ipaddress
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def block_real_network_and_reviewers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every M4 test synthetic while allowing harmless Python subprocesses."""

    real_create_connection = socket.create_connection
    real_socket_connect = socket.socket.connect

    def is_loopback(host: object) -> bool:
        if str(host).lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(str(host)).is_loopback
        except ValueError:
            return False

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(address, tuple) and address and is_loopback(address[0]):
            return real_create_connection(address, *args, **kwargs)
        raise AssertionError("MILESTONE-004 tests may not use non-loopback network")

    def guarded_socket_connect(instance: socket.socket, address: Any) -> Any:
        if instance.family == socket.AF_UNIX or (isinstance(address, tuple) and address and is_loopback(address[0])):
            return real_socket_connect(instance, address)
        raise AssertionError("MILESTONE-004 tests may not use non-loopback network")

    real_popen = subprocess.Popen

    def guarded_popen(args: Any, *positional: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        executable = Path(str(args[0])).name.lower()
        if executable in {"codex", "agy", "gemini"}:
            raise AssertionError(f"real reviewer execution is forbidden: {executable}")
        return real_popen(args, *positional, **kwargs)

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_socket_connect)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
