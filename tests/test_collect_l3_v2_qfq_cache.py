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


class FakeResponse:
    error_code = "0"
    error_msg = "success"

    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.index = -1

    def next(self) -> bool:
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self) -> list[str]:
        return self.rows[self.index]


class FakeLogin:
    def __init__(self, error_code: str = "0", error_msg: str = "success") -> None:
        self.error_code = error_code
        self.error_msg = error_msg


class FakeBaoStock:
    def __init__(self, rows: list[list[str]] | None = None, login: FakeLogin | None = None) -> None:
        self.rows = rows if rows is not None else [
            ["2026-07-09", "10", "11", "9", "10.5", "100", "1"],
            ["2026-07-08", "", "10", "8", "9", "50", "1"],
            ["2026-07-07", "8", "9", "7", "8.5", "75", "0"],
        ]
        self.login_result = login or FakeLogin()
        self.login_count = 0
        self.logout_count = 0
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def login(self) -> FakeLogin:
        self.login_count += 1
        return self.login_result

    def logout(self) -> None:
        self.logout_count += 1

    def query_history_k_data_plus(self, *args: Any, **kwargs: Any) -> FakeResponse:
        self.calls.append((args, kwargs))
        return FakeResponse(self.rows)


def seed_cache(cache_dir: Path, code: str) -> None:
    daily, factors = api_frames()
    frame = cache.build_cache_frame(code, daily, factors, fetched_at="2026-07-11T00:00:00+00:00")
    cache.write_cache(cache_dir, code, frame, "2026-07-09", "2026-07-09")


def test_cache_hit_skips_all_api_calls(tmp_path: Path) -> None:
    db = tmp_path / "tracker.db"
    create_db(db)
    seed_cache(tmp_path / "cache", "000001")
    client = FakeBaoStock()
    messages: list[str] = []

    result = collector.collect(config(db, tmp_path / "cache", ("000001",)), client, messages.append)

    assert result == 0
    assert client.login_count == 0
    assert client.logout_count == 0
    assert client.calls == []
    assert messages == ["SKIP cached complete: 000001"]


def test_atomic_write_cleans_temp_file_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_replace = cache.os.replace

    def fail_csv_replace(source: Path, target: Path) -> None:
        if str(target).endswith("000001.csv"):
            raise OSError("injected replace failure")
        real_replace(source, target)

    monkeypatch.setattr(cache.os, "replace", fail_csv_replace)
    with pytest.raises(cache.CacheWriteError, match="ATOMIC_WRITE_FAILED"):
        cache.write_cache(
            tmp_path,
            "000001",
            cache.build_cache_frame("000001", *api_frames()),
            "2026-07-09",
            "2026-07-09",
        )
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []
    assert not (tmp_path / "000001.meta.json").exists()


def test_resume_skips_complete_codes_and_fetches_pending_only(tmp_path: Path) -> None:
    db = tmp_path / "tracker.db"
    create_db(db)
    cache_dir = tmp_path / "cache"
    seed_cache(cache_dir, "000001")
    client = FakeBaoStock()

    result = collector.collect(config(db, cache_dir, ("000001", "600900")), client, lambda _: None)

    assert result == 0
    assert client.login_count == 1
    assert client.logout_count == 1
    assert len(client.calls) == 1
    args, kwargs = client.calls[0]
    assert args == ("sh.600900", "date,open,high,low,close,volume,tradestatus")
    assert kwargs == {
        "start_date": "2026-07-09",
        "end_date": "2026-07-09",
        "frequency": "d",
        "adjustflag": "2",
    }
    frame, metadata = cache.read_cache(cache_dir, "600900", "2026-07-09", "2026-07-09")
    assert frame["adj_factor"].tolist() == [1.0]
    assert frame["source"].tolist() == ["baostock"]
    assert frame["open"].tolist() == [10.0]
    assert metadata["source"] == "baostock"


def test_baostock_login_failure(tmp_path: Path) -> None:
    db = tmp_path / "tracker.db"
    create_db(db)
    client = FakeBaoStock(login=FakeLogin("1001", "authentication failed"))

    with pytest.raises(collector.CollectorError, match="BaoStock login failed: authentication failed"):
        collector.collect(config(db, tmp_path / "cache", ("000001",)), client, lambda _: None)

    assert client.login_count == 1
    assert client.logout_count == 0
    assert client.calls == []


def test_empty_response_is_cache_miss_without_write(tmp_path: Path) -> None:
    db = tmp_path / "tracker.db"
    create_db(db)
    cache_dir = tmp_path / "cache"
    client = FakeBaoStock(rows=[])
    messages: list[str] = []

    result = collector.collect(config(db, cache_dir, ("000001",)), client, messages.append)

    assert result == 1
    assert client.login_count == 1
    assert client.logout_count == 1
    assert len(client.calls) == 1
    assert "EMPTY_DATA" in messages[-1]
    assert not (cache_dir / "000001.csv").exists()
    assert not (cache_dir / "000001.meta.json").exists()
