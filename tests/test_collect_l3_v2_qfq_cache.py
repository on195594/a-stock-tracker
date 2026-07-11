from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from lib import l3_v2_qfq_cache as cache


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect_l3_v2_qfq_cache.py"
SPEC = importlib.util.spec_from_file_location("collect_l3_v2_qfq_cache", SCRIPT_PATH)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


def create_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE daily_bars (trade_date TEXT, adjusted TEXT)")
    conn.execute("INSERT INTO daily_bars VALUES ('2026-07-09', 'none')")
    conn.execute("CREATE TABLE predictions (code TEXT, framework TEXT, score_date TEXT)")
    conn.commit()
    conn.close()


def api_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = pd.DataFrame(
        [{"trade_date": "20260709", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 100}]
    )
    factors = pd.DataFrame([{"trade_date": "20260709", "adj_factor": 1.0}])
    return daily, factors


def config(db: Path, cache_dir: Path, codes: tuple[str, ...]) -> Any:
    return collector.CollectorConfig(
        db_path=db,
        start=date(2026, 7, 9),
        end=date(2026, 7, 9),
        codes=codes,
        cache_dir=cache_dir,
    )


class FakeLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def acquire(self) -> None:
        self.calls += 1


class FakePro:
    def __init__(self, rate_limit: bool = False) -> None:
        self.daily_calls: list[str] = []
        self.factor_calls: list[str] = []
        self.rate_limit = rate_limit

    def daily(self, **kwargs: str) -> pd.DataFrame:
        self.daily_calls.append(kwargs["ts_code"])
        return api_frames()[0]

    def adj_factor(self, **kwargs: str) -> pd.DataFrame:
        self.factor_calls.append(kwargs["ts_code"])
        if self.rate_limit:
            raise RuntimeError("频率超限(1次/分钟)")
        return api_frames()[1]


def seed_cache(cache_dir: Path, code: str) -> None:
    daily, factors = api_frames()
    frame = cache.build_cache_frame(code, daily, factors, fetched_at="2026-07-11T00:00:00+00:00")
    cache.write_cache(cache_dir, code, frame, "2026-07-09", "2026-07-09")


def test_cache_hit_skips_all_api_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "tracker.db"
    create_db(db)
    seed_cache(tmp_path / "cache", "000001")
    pro = FakePro()
    messages: list[str] = []
    monkeypatch.setattr(collector, "is_rate_limit", collector.is_rate_limit)
    result = collector.collect(config(db, tmp_path / "cache", ("000001",)), pro, messages.append, FakeLimiter())
    assert result == 0
    assert pro.daily_calls == []
    assert pro.factor_calls == []
    assert messages == ["SKIP cached complete: 000001"]


def test_rate_limit_exits_nonzero_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "tracker.db"
    create_db(db)
    pro = FakePro(rate_limit=True)
    messages: list[str] = []
    monkeypatch.setattr(collector, "MIN_PRELOAD_TRADING_DAYS", 120)
    result = collector.collect(config(db, tmp_path / "cache", ("000001",)), pro, messages.append, FakeLimiter())
    assert result != 0
    assert pro.daily_calls == ["000001.SZ"]
    assert pro.factor_calls == ["000001.SZ"]
    assert "TUSHARE_RATE_LIMIT" in messages[-1]
    assert not (tmp_path / "cache" / "000001.meta.json").exists()


def test_atomic_write_cleans_temp_file_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_replace = cache.os.replace

    def fail_csv_replace(source: Path, target: Path) -> None:
        if str(target).endswith("000001.csv"):
            raise OSError("injected replace failure")
        real_replace(source, target)

    monkeypatch.setattr(cache.os, "replace", fail_csv_replace)
    with pytest.raises(cache.CacheWriteError, match="ATOMIC_WRITE_FAILED"):
        cache.write_cache(tmp_path, "000001", cache.build_cache_frame("000001", *api_frames()), "2026-07-09", "2026-07-09")
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []
    assert not (tmp_path / "000001.meta.json").exists()


def test_resume_skips_complete_codes_and_fetches_pending_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "tracker.db"
    create_db(db)
    cache_dir = tmp_path / "cache"
    seed_cache(cache_dir, "000001")
    pro = FakePro()
    limiter = FakeLimiter()
    monkeypatch.setattr(collector, "validate_code", cache.validate_code)
    result = collector.collect(config(db, cache_dir, ("000001", "600900")), pro, lambda _: None, limiter)
    assert result == 0
    assert pro.daily_calls == ["600900.SH"]
    assert pro.factor_calls == ["600900.SH"]
    assert limiter.calls == 1
    cache.read_cache(cache_dir, "600900", "2026-07-09", "2026-07-09")
