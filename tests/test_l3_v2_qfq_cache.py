from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from lib import l3_v2_qfq_cache as cache


def sample_frame(code: str = "000001") -> pd.DataFrame:
    daily = pd.DataFrame(
        [
            {"trade_date": "20260709", "open": 10, "high": 12, "low": 9, "close": 11, "vol": 1000},
            {"trade_date": "20260710", "open": 20, "high": 24, "low": 18, "close": 22, "vol": 2000},
        ]
    )
    factors = pd.DataFrame(
        [
            {"trade_date": "20260709", "adj_factor": 1.0},
            {"trade_date": "20260710", "adj_factor": 2.0},
        ]
    )
    return cache.build_cache_frame(code, daily, factors, fetched_at="2026-07-11T00:00:00+00:00")


def test_rate_limiter_resamples_clock_after_sleep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"mono": 0.0, "wall": 100.0}
    sleeps: list[float] = []
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock["mono"])
    monkeypatch.setattr(cache.time, "time", lambda: clock["wall"])

    def fake_sleep(wait: float) -> None:
        sleeps.append(wait)
        clock["mono"] += wait
        clock["wall"] += wait

    monkeypatch.setattr(cache.time, "sleep", fake_sleep)
    limiter = cache.FixedIntervalRateLimiter(tmp_path)
    limiter.acquire()
    clock["mono"] += 1
    clock["wall"] += 1
    pre_sleep_wall = clock["wall"]
    limiter.acquire()

    state = json.loads((tmp_path / ".adj_factor_rate_state.json").read_text())
    assert sleeps == [60.0]
    assert state["last_request_started_epoch"] >= pre_sleep_wall + sleeps[0]


def test_cache_write_read_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache.LOGGER, "debug", lambda *args, **kwargs: None)
    expected = sample_frame()
    metadata = cache.write_cache(tmp_path, "000001", expected, "2026-07-09", "2026-07-10")
    actual, loaded_metadata = cache.read_cache(tmp_path, "000001", "2026-07-09", "2026-07-10")
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False)
    assert loaded_metadata == metadata
    assert metadata["status"] == "complete"


def test_write_cache_restores_csv_on_metadata_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = sample_frame()
    cache.write_cache(tmp_path, "000001", original, "2026-07-09", "2026-07-10")
    original_csv = (tmp_path / "000001.csv").read_bytes()
    real_atomic_write = cache._atomic_write_bytes

    def fail_metadata_write(path: Path, payload: bytes) -> None:
        if path.name.endswith(".meta.json"):
            raise OSError("metadata write failed")
        real_atomic_write(path, payload)

    monkeypatch.setattr(cache, "_atomic_write_bytes", fail_metadata_write)
    updated = sample_frame()
    updated.loc[0, "close"] = 99

    with pytest.raises(cache.CacheWriteError, match="ATOMIC_WRITE_FAILED"):
        cache.write_cache(tmp_path, "000001", updated, "2026-07-09", "2026-07-10")

    restored, _ = cache.read_cache(tmp_path, "000001", "2026-07-09", "2026-07-10")
    assert (tmp_path / "000001.csv").read_bytes() == original_csv
    pd.testing.assert_frame_equal(restored, original, check_dtype=False)


def test_checksum_corruption_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache.write_cache(tmp_path, "000001", sample_frame(), "2026-07-09", "2026-07-10")
    csv_path = tmp_path / "000001.csv"
    csv_path.write_bytes(csv_path.read_bytes() + b"corrupt")
    with pytest.raises(cache.CacheInvalidError, match="CHECKSUM_MISMATCH"):
        cache.read_cache(tmp_path, "000001", "2026-07-09", "2026-07-10")


def test_incomplete_window_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "SCHEMA_VERSION", cache.SCHEMA_VERSION)
    cache.write_cache(tmp_path, "000001", sample_frame(), "2026-07-09", "2026-07-10")
    with pytest.raises(cache.CacheInvalidError, match="INCOMPLETE_WINDOW"):
        cache.read_cache(tmp_path, "000001", "2026-07-08", "2026-07-10")


def test_qfq_derivation_adjusts_prices_and_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    raw = sample_frame()
    adjusted = cache.derive_qfq_ohlcv(raw)
    assert adjusted.loc[0, "open"] == pytest.approx(5.0)
    assert adjusted.loc[0, "close"] == pytest.approx(5.5)
    assert adjusted.loc[0, "vol"] == pytest.approx(2000.0)
    assert adjusted.loc[1, "open"] == pytest.approx(20.0)
    assert adjusted.loc[1, "vol"] == pytest.approx(2000.0)
    assert raw.loc[0, "open"] == 10


def test_file_lock_rejects_second_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache.LOGGER, "warning", lambda *args, **kwargs: None)
    first = cache.CollectorLock(tmp_path)
    second = cache.CollectorLock(tmp_path)
    with first:
        with pytest.raises(cache.LockUnavailableError, match="COLLECTOR_LOCKED"):
            second.acquire()
    with second:
        assert (tmp_path / ".collector.lock").exists()


def test_invalid_code_cannot_be_used_as_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(cache.CacheInvalidError, match="INVALID_CODE"):
        cache.read_cache(tmp_path, "../token", "2026-07-09", "2026-07-10")
