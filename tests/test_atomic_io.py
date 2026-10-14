"""Shared atomic-publication safety regressions, using only temporary files."""

import pytest

from a_stock_tracker import atomic_io as script


def test_atomic_write_restores_previous_file_after_post_replace_failure(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "evidence.json"
    target.write_bytes(b"previous-evidence\n")
    real_fsync = script.os.fsync
    calls = 0

    def fail_first_directory_sync(file_descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory fsync failure")
        real_fsync(file_descriptor)

    monkeypatch.setattr(script.os, "fsync", fail_first_directory_sync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        script._atomic_write_bytes(target, b"new-evidence\n")

    assert target.read_bytes() == b"previous-evidence\n"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.rollback"))


@pytest.mark.parametrize("existing", [False, True])
def test_atomic_write_publishes_and_cleans_temporary_files(tmp_path, existing) -> None:
    target = tmp_path / "nested" / "evidence.json"
    if existing:
        target.parent.mkdir()
        target.write_bytes(b"previous-evidence\n")

    script._atomic_write_bytes(target, b"new-evidence\n")

    assert target.read_bytes() == b"new-evidence\n"
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("existing", [False, True])
def test_atomic_write_file_sync_failure_never_publishes(tmp_path, monkeypatch, existing) -> None:
    target = tmp_path / "evidence.json"
    if existing:
        target.write_bytes(b"previous-evidence\n")

    def fail_sync(file_descriptor: int) -> None:
        raise OSError("injected file fsync failure")

    monkeypatch.setattr(script.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="injected file fsync failure"):
        script._atomic_write_bytes(target, b"new-evidence\n")

    assert target.read_bytes() == b"previous-evidence\n" if existing else not target.exists()
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.rollback"))


def test_atomic_write_removes_new_file_after_directory_sync_failure(tmp_path, monkeypatch) -> None:
    target = tmp_path / "evidence.json"
    real_fsync = script.os.fsync
    calls = 0

    def fail_first_directory_sync(file_descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory fsync failure")
        real_fsync(file_descriptor)

    monkeypatch.setattr(script.os, "fsync", fail_first_directory_sync)
    with pytest.raises(OSError, match="injected directory fsync failure"):
        script._atomic_write_bytes(target, b"new-evidence\n")

    assert list(tmp_path.iterdir()) == []


def test_atomic_write_reports_rollback_failure(tmp_path, monkeypatch) -> None:
    target = tmp_path / "evidence.json"
    target.write_bytes(b"previous-evidence\n")
    real_fsync = script.os.fsync
    calls = 0

    def fail_directory_and_rollback_sync(file_descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("injected rollback fsync failure")
        real_fsync(file_descriptor)

    monkeypatch.setattr(script.os, "fsync", fail_directory_and_rollback_sync)
    with pytest.raises(RuntimeError, match="atomic write failed and rollback failed"):
        script._atomic_write_bytes(target, b"new-evidence\n")

    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.rollback"))
