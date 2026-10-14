"""Shared atomic file publication with durable replacement and rollback."""

import os
from pathlib import Path


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    rollback = path.with_name(f".{path.name}.{os.getpid()}.rollback")
    previous = path.read_bytes() if path.is_file() else None
    replaced = False
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if replaced:
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    with rollback.open("wb") as handle:
                        handle.write(previous)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(rollback, path)
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except Exception as rollback_error:
                raise RuntimeError(
                    f"atomic write failed and rollback failed: {path}"
                ) from rollback_error
        raise
    finally:
        temporary.unlink(missing_ok=True)
        rollback.unlink(missing_ok=True)
