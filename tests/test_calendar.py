"""Retained calendar evidence, failure and atomic-publication regression tests."""

import hashlib
import json
from datetime import date, timedelta
from unittest.mock import Mock

import pandas as pd
import pytest

from a_stock_tracker import calendar as script


def _calendar_response(start: date, end: date) -> pd.DataFrame:
    rows = []
    previous_open = None
    current = start
    while current <= end:
        is_open = int(current.weekday() < 5)
        rows.append(
            {
                "exchange": "SSE",
                "cal_date": current.strftime("%Y%m%d"),
                "is_open": is_open,
                "pretrade_date": previous_open,
            }
        )
        if is_open:
            previous_open = current.strftime("%Y%m%d")
        current += timedelta(days=1)
    return pd.DataFrame(reversed(rows))


def test_calendar_refresh_writes_auditable_runtime_evidence(tmp_path) -> None:
    tracked = tmp_path / "config" / "trading_calendar.json"
    runtime = tmp_path / "data" / "trading_calendar.json"
    sources = tmp_path / "data" / "trading-calendar-sources"
    tracked.parent.mkdir()
    tracked.write_text(
        json.dumps(
            {
                "dates": ["2026-09-18"],
                "covered_from": "2026-09-18",
                "covered_to": "2026-09-18",
                "as_of": "2026-09-18",
                "source": "seed",
            }
        ),
        encoding="utf-8",
    )
    api = Mock()
    api.trade_cal.return_value = _calendar_response(date(2026, 9, 18), date(2026, 9, 20))

    result = script.refresh_calendar(
        api,
        date(2026, 9, 20),
        tracked_path=tracked,
        runtime_path=runtime,
        source_dir=sources,
    )

    evidence = json.loads(result.read_text(encoding="utf-8"))
    raw_files = list(sources.glob("*.json"))
    assert evidence["covered_from"] == "2026-09-18"
    assert evidence["covered_to"] == "2026-09-20"
    assert evidence["as_of"] == "2026-09-20"
    assert evidence["dates"] == ["2026-09-18"]
    assert len(raw_files) == 1
    raw_hash = hashlib.sha256(raw_files[0].read_bytes()).hexdigest()
    raw_payload = json.loads(raw_files[0].read_text(encoding="utf-8"))
    assert raw_files[0].stem == raw_hash
    assert raw_payload["official_cross_check"] == {
        "notice": script.CALENDAR_OFFICIAL_REFERENCE,
        "covered_from": "2026-09-18",
        "covered_to": "2026-09-20",
        "closure_conflicts": [],
    }
    assert f"raw_sha256={raw_hash}" in evidence["source"]
    assert script.CALENDAR_OFFICIAL_REFERENCE in evidence["source"]
    assert "official_cross_check_range=2026-09-18..2026-09-20" in evidence["source"]
    api.trade_cal.assert_called_once_with(
        exchange="SSE",
        start_date="20260918",
        end_date="20260920",
        fields="exchange,cal_date,is_open,pretrade_date",
    )


def test_calendar_refresh_failure_preserves_previous_runtime_file(tmp_path) -> None:
    tracked = tmp_path / "tracked.json"
    runtime = tmp_path / "runtime.json"
    tracked.write_text(
        json.dumps(
            {
                "dates": ["2026-09-18"],
                "covered_from": "2026-09-18",
                "covered_to": "2026-09-18",
                "as_of": "2026-09-18",
                "source": "seed",
            }
        ),
        encoding="utf-8",
    )
    runtime.write_bytes(b"previous-calendar\n")
    api = Mock()
    incomplete = _calendar_response(date(2026, 9, 18), date(2026, 9, 20))
    api.trade_cal.return_value = incomplete[incomplete["cal_date"] != "20260919"]

    with pytest.raises(RuntimeError, match="does not cover every natural date"):
        script.refresh_calendar(
            api,
            date(2026, 9, 20),
            tracked_path=tracked,
            runtime_path=runtime,
            source_dir=tmp_path / "sources",
        )

    assert runtime.read_bytes() == b"previous-calendar\n"


def test_atomic_write_restores_previous_file_after_post_replace_failure(
    tmp_path, monkeypatch
) -> None:
    target = tmp_path / "calendar.json"
    target.write_bytes(b"previous-calendar\n")
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
        script._atomic_write_bytes(target, b"new-calendar\n")

    assert target.read_bytes() == b"previous-calendar\n"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.rollback"))


def test_calendar_refresh_rejects_official_closure_marked_open(tmp_path) -> None:
    tracked = tmp_path / "tracked.json"
    tracked.write_text(
        json.dumps(
            {
                "dates": [],
                "covered_from": "2026-02-15",
                "covered_to": "2026-02-15",
                "as_of": "2026-02-15",
                "source": "seed",
            }
        ),
        encoding="utf-8",
    )
    api = Mock()
    api.trade_cal.return_value = _calendar_response(date(2026, 2, 15), date(2026, 2, 16))

    with pytest.raises(RuntimeError, match="春节:2026-02-16:marked_open"):
        script.refresh_calendar(
            api,
            date(2026, 2, 16),
            tracked_path=tracked,
            runtime_path=tmp_path / "runtime.json",
            source_dir=tmp_path / "sources",
        )
