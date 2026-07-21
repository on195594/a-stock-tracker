from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from a_stock_tracker.config import (
    FEATURE_FLAG_DIVIDEND_DOMAIN,
    FEATURE_FLAG_FINANCIAL_DOMAIN,
    FEATURE_FLAG_VALUATION_DOMAIN,
    get_materialization_feature_flag,
)
from a_stock_tracker.data import tushare_primary_cache
from a_stock_tracker.data import tushare_primary_materialization as tpm


class DummyException(Exception):
    """Compatibility marker for expected failure paths."""


def _create_shadow_run(db_path: Path, endpoint: str) -> int:
    with tushare_primary_cache.open_shadow_store(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ingestion_runs (endpoint, request_fingerprint, requested_at, status, row_count) VALUES (?, ?, ?, 'completed', 0)",
            (endpoint, f"{endpoint}-fingerprint", datetime.now(UTC).isoformat()),
        )
        run_id = cur.lastrowid
        assert run_id is not None
        conn.commit()
    return int(run_id)


def _insert_valuation(
    conn: sqlite3.Connection,
    run_id: int,
    code: str,
    trade_date: str,
    close: float,
    pe_ttm: float | None,
    pb: float | None,
    total_mv: float,
    circ_mv: float,
    dv_ttm: float | None,
    source: str,
    source_as_of: str,
    observed_at: str,
) -> None:
    row = {
        "ts_code": f"{code}.SH",
        "trade_date": trade_date,
        "close": close,
        "pe": 99.0,
        "pe_ttm": pe_ttm,
        "pb": pb,
        "ps": None,
        "ps_ttm": None,
        "dv_ratio": None,
        "dv_ttm": dv_ttm,
        "total_mv": total_mv,
        "circ_mv": circ_mv,
    }
    tushare_primary_cache.persist_observations(
        conn,
        "daily_basic",
        [row],
        run_id,
        observed_at,
        "backfilled_latest",
        source,
        source_as_of,
    )


def _insert_fina_indicator(
    conn: sqlite3.Connection,
    run_id: int,
    code: str,
    ann_date: str,
    end_date: str,
    roe_waa: float,
    netprofit_yoy: float,
    debt_to_assets: float,
    grossprofit_margin: float,
    source: str,
    source_as_of: str,
    observed_at: str,
    f_ann_date: str | None = None,
) -> None:
    row = {
        "ts_code": f"{code}.SH",
        "ann_date": ann_date,
        "f_ann_date": f_ann_date,
        "end_date": end_date,
        "report_type": "N",
        "comp_type": "C",
        "end_type": "Q1",
        "update_flag": "0",
        "roe_waa": roe_waa,
        "netprofit_yoy": netprofit_yoy,
        "debt_to_assets": debt_to_assets,
        "grossprofit_margin": grossprofit_margin,
    }
    tushare_primary_cache.persist_observations(
        conn,
        "fina_indicator",
        [row],
        run_id,
        observed_at,
        "backfilled_latest",
        source,
        source_as_of,
    )


def _insert_dividend(
    conn: sqlite3.Connection,
    run_id: int,
    code: str,
    ann_date: str,
    end_date: str,
    record_date: str,
    ex_date: str,
    div_proc: str,
    cash_div_tax: float,
    source: str,
    source_as_of: str,
    observed_at: str,
) -> None:
    row = {
        "ts_code": f"{code}.SH",
        "ann_date": ann_date,
        "end_date": end_date,
        "record_date": record_date,
        "ex_date": ex_date,
        "div_proc": div_proc,
        "cash_div_tax": cash_div_tax,
        "cash_div": cash_div_tax * 0.7,
    }
    tushare_primary_cache.persist_observations(
        conn,
        "dividend",
        [row],
        run_id,
        observed_at,
        "backfilled_latest",
        source,
        source_as_of,
    )


def _build_tracker_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS stock_fundamentals (
            code TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            data JSON,
            updated_at TEXT,
            ttl_hours INTEGER DEFAULT 168
        )"""
        )
        conn.execute(
            """INSERT OR REPLACE INTO stock_fundamentals (code, name, industry, data, updated_at, ttl_hours)
            VALUES ('600036', '招商银行', '银行', '{\"pe_ttm\": 8.0}', '2000-01-01T00:00:00', 168)"""
        )
        conn.commit()


def test_feature_flags_are_runtime_only_off_by_default() -> None:
    assert get_materialization_feature_flag(FEATURE_FLAG_VALUATION_DOMAIN) is False
    assert get_materialization_feature_flag(FEATURE_FLAG_FINANCIAL_DOMAIN) is False
    assert get_materialization_feature_flag(FEATURE_FLAG_DIVIDEND_DOMAIN) is False


def test_feature_flags_can_toggle_without_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FEATURE_FLAG_VALUATION_DOMAIN, "on")
    monkeypatch.setenv(FEATURE_FLAG_FINANCIAL_DOMAIN, "off")
    monkeypatch.setenv(FEATURE_FLAG_DIVIDEND_DOMAIN, "on")
    assert get_materialization_feature_flag(FEATURE_FLAG_VALUATION_DOMAIN) is True
    assert get_materialization_feature_flag(FEATURE_FLAG_FINANCIAL_DOMAIN) is False
    assert get_materialization_feature_flag(FEATURE_FLAG_DIVIDEND_DOMAIN) is True


def test_filter_future_financial_announcements(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "fina_indicator")
        # past valid
        _insert_fina_indicator(
            conn,
            run_id,
            "600036",
            "2025-06-01",
            "20241231",
            12.0,
            8.0,
            40.0,
            28.0,
            "tushare.fina_indicator",
            "2025-06-01",
            "2025-06-01T00:00:00",
        )
        # future should be excluded by as-of filter
        _insert_fina_indicator(
            conn,
            run_id,
            "600036",
            "2027-01-03",
            "20261231",
            99.0,
            66.0,
            39.0,
            30.0,
            "tushare.fina_indicator",
            "2027-01-03",
            "2027-01-03T00:00:00",
        )
        conn.commit()

    result = tpm.build_stock_fundamentals_payload(
        shadow_db_path=shadow,
        as_of_date="2026-07-21",
        watchlist=("600036",),
        execute=False,
        target_db_path=tmp_path / "tracker.db",
    )
    assert result["600036"]["financial"]["roe_3y_avg"] == 12.0
    assert result["600036"]["financial"]["report_period"] == "20241231"


def test_financial_correction_uses_latest_observed_for_same_period(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "fina_indicator")
        _insert_fina_indicator(
            conn,
            run_id,
            "600036",
            "2025-06-01",
            "20241231",
            10.0,
            8.0,
            50.0,
            20.0,
            "tushare",
            "2025-06-01",
            "2025-06-01T10:00:00",
        )
        # correction record observed later
        _insert_fina_indicator(
            conn,
            run_id,
            "600036",
            "2025-06-02",
            "20241231",
            20.0,
            9.0,
            40.0,
            21.0,
            "tushare",
            "2025-06-02",
            "2025-06-02T10:00:00",
        )
        conn.commit()

    result = tpm.build_stock_fundamentals_payload(
        shadow_db_path=shadow,
        as_of_date="2026-07-21",
        watchlist=("600036",),
        execute=False,
        target_db_path=tmp_path / "tracker.db",
        financial_enabled=True,
    )
    assert result["600036"]["financial"]["roe_latest"] == 20.0
    assert result["600036"]["financial"]["financial_source_as_of"] == "2025-06-02"


def test_annual_means_use_only_year_end_1231(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "fina_indicator")
        # two quarter-like records and one annual should include only annual
        _insert_fina_indicator(
            conn,
            run_id,
            "600036",
            "2026-06-01",
            "20240630",
            1.0,
            1.0,
            30.0,
            12.0,
            "tushare",
            "2026-06-01",
            "2026-06-01T00:00:00",
        )
        _insert_fina_indicator(
            conn,
            run_id,
            "600036",
            "2026-03-01",
            "20241231",
            12.0,
            2.0,
            35.0,
            18.0,
            "tushare",
            "2026-03-01",
            "2026-03-01T00:00:00",
        )
        _insert_fina_indicator(
            conn,
            run_id,
            "600036",
            "2026-04-01",
            "20241231",
            15.0,
            5.0,
            34.0,
            19.0,
            "tushare",
            "2026-04-01",
            "2026-04-01T00:00:00",
        )
        _insert_fina_indicator(
            conn,
            run_id,
            "600036",
            "2026-05-01",
            "20231231",
            18.0,
            7.0,
            33.0,
            22.0,
            "tushare",
            "2026-05-01",
            "2026-05-01T00:00:00",
        )
        conn.commit()

    payload = tpm.build_stock_fundamentals_payload(
        shadow_db_path=shadow,
        as_of_date="2026-07-21",
        watchlist=("600036",),
        execute=False,
        target_db_path=tmp_path / "tracker.db",
        financial_enabled=True,
    )
    assert payload["600036"]["financial"]["roe_3y_avg"] == 16.5
    assert payload["600036"]["financial"]["net_profit_growth"] == 6.0


def test_full_10y_pb_window_populates_legacy_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shadow = tmp_path / "shadow.db"
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "daily_basic")
        for month in range(120):
            day = 1 + (month % 28)
            year = 2016 + month // 12
            month_number = month % 12 + 1
            date = f"{year}-{month_number:02d}-{day:02d}"
            _insert_valuation(
                conn,
                run_id,
                "600036",
                date,
                close=10.0,
                pe_ttm=6.0 + month,
                pb=1.0 + month / 100,
                total_mv=1000.0 + month,
                circ_mv=800.0 + month,
                dv_ttm=2.0,
                source="tushare.daily_basic",
                source_as_of="2016-01-01",
                observed_at="2026-07-20T00:00:00",
            )
        conn.commit()

    monkeypatch.setattr(
        tpm,
        "_compute_valuation_percentile",
        lambda *args, **kwargs: 42.0,
    )
    payload = tpm.build_stock_fundamentals_payload(
        shadow_db_path=shadow,
        as_of_date="2026-07-21",
        watchlist=("600036",),
        execute=False,
        target_db_path=tmp_path / "tracker.db",
        valuation_enabled=True,
    )
    val = payload["600036"]["valuation"]
    assert val["valuation_coverage_status"] == "FULL_10Y"
    assert val["pb_percentile_10y"] == 42.0


def test_financial_gross_margin_allowed_empty_for_financial_industry(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "fina_indicator")
        _insert_fina_indicator(
            conn,
            run_id,
            "601988",
            "2025-06-01",
            "20241231",
            15.0,
            3.0,
            48.0,
            55.0,
            "tushare",
            "2025-06-01",
            "2025-06-01T00:00:00",
        )
        conn.commit()

    payload = tpm.build_stock_fundamentals_payload(
        shadow_db_path=shadow,
        as_of_date="2026-07-21",
        watchlist=("601988",),
        execute=False,
        target_db_path=tmp_path / "tracker.db",
        financial_enabled=True,
        watchlist_industries={"601988": "银行"},
    )
    assert payload["601988"]["financial"].get("gross_margin") is None


def test_dividend_uses_cash_div_tax_when_implemented_and_ex_date_before_asof(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "dividend")
        _insert_dividend(
            conn,
            run_id,
            "600036",
            "2026-03-01",
            "2025-12-31",
            "2026-03-15",
            "2026-05-01",
            "实施",
            12.0,
            "tushare.dividend",
            "2026-03-01",
            "2026-05-01T00:00:00",
        )
        _insert_dividend(
            conn,
            run_id,
            "600036",
            "2026-04-01",
            "2026-12-31",
            "2026-04-15",
            "2026-08-01",
            "实施",
            20.0,
            "tushare.dividend",
            "2026-04-01",
            "2026-07-01T00:00:00",
        )
        # should be ignored
        _insert_dividend(
            conn,
            run_id,
            "600036",
            "2026-01-01",
            "2025-12-31",
            "2026-01-15",
            "2026-06-01",
            "预案",
            99.0,
            "tushare.dividend",
            "2026-01-01",
            "2026-06-01T00:00:00",
        )
        conn.commit()

    payload = tpm.build_stock_fundamentals_payload(
        shadow_db_path=shadow,
        as_of_date="2026-07-15",
        watchlist=("600036",),
        execute=False,
        target_db_path=tmp_path / "tracker.db",
        dividend_enabled=True,
    )
    div = payload["600036"]["dividend"]
    assert div["dps"] == 12.0
    assert div["dividend_ex_date"] == "2026-05-01"


def test_preview_does_not_write_target_db_and_shows_patch(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    tracker = tmp_path / "tracker.db"
    _build_tracker_db(tracker)

    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "daily_basic")
        _insert_valuation(
            conn,
            run_id,
            "600036",
            "2026-07-20",
            25.0,
            10.0,
            2.0,
            1000.0,
            500.0,
            2.5,
            "tushare.daily_basic",
            "2026-07-20",
            "2026-07-20T00:00:00",
        )
        conn.commit()

    result = tpm.run_materialization(
        shadow_db_path=shadow,
        as_of_date="2026-07-21",
        target_db_path=tracker,
        execute=False,
        valuation_enabled=True,
        watchlist=("600036",),
    )

    preview = json.loads(result.preview_json)
    assert preview["as_of_date"] == "2026-07-21"
    assert preview["changed_count"] == 1
    valuation = preview["domains"]["600036"]["valuation"]
    assert valuation["valuation_coverage_status"] == "INSUFFICIENT_HISTORY"
    assert valuation["pb_percentile_10y"] is None

    with sqlite3.connect(tracker) as conn:
        row = conn.execute("SELECT data FROM stock_fundamentals WHERE code='600036'").fetchone()
        assert row is not None
        data = json.loads(row[0])
        assert data["pe_ttm"] == 8.0


def test_execute_fails_and_rolls_back_if_required_field_missing(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    tracker = tmp_path / "tracker.db"
    _build_tracker_db(tracker)
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "fina_indicator")
        _insert_fina_indicator(
            conn,
            run_id,
            "600036",
            "2025-06-01",
            "20231231",
            roe_waa=5.0,
            netprofit_yoy=1.0,
            debt_to_assets=10.0,
            grossprofit_margin=22.0,
            source="tushare.fina_indicator",
            source_as_of="2025-06-01",
            observed_at="2025-06-01T00:00:00",
        )
        run_id2 = _create_shadow_run(shadow, "fina_indicator")
        _insert_fina_indicator(
            conn,
            run_id2,
            "600036",
            "2026-06-01",
            "20261231",
            roe_waa=9.0,
            netprofit_yoy=4.0,
            debt_to_assets=40.0,
            grossprofit_margin=30.0,
            source="tushare.fina_indicator",
            source_as_of="2026-06-01",
            observed_at="2026-06-01T00:00:00",
        )
        conn.commit()

    with pytest.raises(tpm.MaterializationReadinessError):
        tpm.run_materialization(
            shadow_db_path=shadow,
            as_of_date="2026-07-21",
            target_db_path=tracker,
            execute=True,
            financial_enabled=True,
            watchlist=("600036",),
            watchlist_industries={"600036": "制造业"},
        )

    with sqlite3.connect(tracker) as conn:
        row = conn.execute("SELECT data FROM stock_fundamentals WHERE code='600036'").fetchone()
        assert row is not None
        data = json.loads(row[0])
        # unchanged baseline should be preserved on rollback
        assert data["pe_ttm"] == 8.0
