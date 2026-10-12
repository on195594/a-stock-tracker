"""Bound pipe memory while retaining subprocess deadlines and error semantics."""

import os
import selectors
import subprocess
import time

from a_stock_tracker.research import ScreenError


def run_bounded(args, *, timeout, check=False, cwd=None, env=None, max_bytes=8_000_000):
    deadline = time.monotonic() + timeout
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    size = 0
    with subprocess.Popen(
        args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
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
