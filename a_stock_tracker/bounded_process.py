"""Bound pipe memory while retaining subprocess deadlines and error semantics."""

import os
import selectors
import stat
import subprocess
import time

from a_stock_tracker.research import ScreenError

# Only the receiver's batch process gets this marker. External commands retain
# the single lease FD but cannot propagate it via another run_bounded invocation.
EXECUTION_LEASE_ENV = "RESEARCH_SERVICE_EXECUTION_LEASE"


def execution_lease_environment(fd: int) -> dict[str, str]:
    info = os.fstat(fd)
    return {**os.environ, EXECUTION_LEASE_ENV: f"{fd}:{info.st_dev}:{info.st_ino}"}


def _execution_lease_fds() -> tuple[int, ...]:
    marker = os.environ.get(EXECUTION_LEASE_ENV)
    if marker is None:
        return ()  # Standalone CLI calls have no receiver lease.
    try:
        fd, device, inode = map(int, marker.split(":"))
        info = os.fstat(fd)
        if (
            fd <= 2
            or not stat.S_ISREG(info.st_mode)
            or (info.st_dev, info.st_ino) != (device, inode)
        ):
            raise ValueError("Invalid execution lease identity")
    except (OSError, ValueError) as exc:
        raise ScreenError("Invalid inherited execution lease") from exc
    return (fd,)


def run_bounded(args, *, timeout, check=False, cwd=None, env=None, max_bytes=8_000_000):
    deadline = time.monotonic() + timeout
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    size = 0
    lease_fds = _execution_lease_fds()
    child_env = dict(os.environ if env is None else env)
    child_env.pop(EXECUTION_LEASE_ENV, None)
    with subprocess.Popen(
        args,
        cwd=cwd,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=lease_fds,
    ) as p:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(p.stdout, selectors.EVENT_READ, "stdout")
                selector.register(p.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, timeout)
                    for key, _ in selector.select(remaining):
                        data = os.read(key.fd, 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        size += len(data)
                        if size > max_bytes:
                            raise ScreenError("Subprocess output budget exceeded")
                        chunks[key.data].extend(data)
            code = p.wait(timeout=max(0, deadline - time.monotonic()))
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()
    result = subprocess.CompletedProcess(
        args, code, chunks["stdout"].decode("utf-8"), chunks["stderr"].decode("utf-8")
    )
    if check:
        result.check_returncode()
    return result
