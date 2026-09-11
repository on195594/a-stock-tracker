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


def _create_shadow_run(db_path: Path, endpoint: str, source_as_of: str | None = None) -> int:
    with tushare_primary_cache.open_shadow_store(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO ingestion_runs
            (endpoint, request_fingerprint, requested_at, status, row_count, source_as_of)
            VALUES (?, ?, ?, 'completed', 0, ?)""",
            (endpoint, f"{endpoint}-fingerprint", datetime.now(UTC).isoformat(), source_as_of),
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
    bps: float = 10.0,
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
        "bps": bps,
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
    *,
    stk_div: float | None = None,
    stk_bo_rate: float | None = None,
    stk_co_rate: float | None = None,
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
        "stk_div": stk_div,
        "stk_bo_rate": stk_bo_rate,
        "stk_co_rate": stk_co_rate,
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


def _build_tracker_db(db_path: Path, codes: tuple[str, ...] = ("600036",)) -> None:
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
        for code in codes:
            conn.execute(
                """INSERT OR REPLACE INTO stock_fundamentals
                (code, name, industry, data, updated_at, ttl_hours)
                VALUES (?, ?, '银行', '{"pe_ttm": 8.0}', '2000-01-01T00:00:00', 168)""",
                (code, f"测试-{code}"),
            )
        conn.commit()


def test_feature_flags_are_runtime_only_off_by_default() -> None:
    assert get_materialization_feature_flag(FEATURE_FLAG_VALUATION_DOMAIN) is False
    assert get_materialization_feature_flag(FEATURE_FLAG_FINANCIAL_DOMAIN) is False
    assert get_materialization_feature_flag(FEATURE_FLAG_DIVIDEND_DOMAIN) is False


def test_materialization_connections_are_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shadow = tmp_path / "shadow.db"
    with tushare_primary_cache.open_shadow_store(shadow):
        pass
    tracker = tmp_path / "tracker.db"
    _build_tracker_db(tracker)
    connections: list[sqlite3.Connection] = []
    original_connect = sqlite3.connect

    def recording_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(tpm.sqlite3, "connect", recording_connect)

    tpm.build_stock_fundamentals_payload(
        shadow_db_path=shadow,
        as_of_date="2026-07-21",
        watchlist=(),
        execute=False,
        target_db_path=tracker,
    )
    tpm._write_payload(tracker, {})

    assert len(connections) == 2
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def test_load_rows_preserves_valuation_without_observation_event(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO valuation_observations
            (record_key, code, trade_date, close, pe, pe_ttm, pb, ps, ps_ttm,
             dv_ratio, dv_ttm, total_mv, circ_mv, source, source_as_of, payload_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "unlinked-valuation",
                "600036",
                "20260721",
                42.0,
                8.0,
                8.0,
                1.0,
                None,
                None,
                None,
                None,
                1000.0,
                800.0,
                "tushare.daily_basic",
                "2026-07-21",
                "payload-sha",
            ),
        )

        rows = tpm._load_rows(conn, "valuation_observations", "600036")

    assert len(rows) == 1
    assert rows[0]["record_key"] == "unlinked-valuation"
    assert rows[0]["observed_at"] == ""
    assert rows[0]["run_source_as_of"] is None


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
        for month in range(121):
            year = 2016 + (month + 6) // 12
            month_number = (month + 6) % 12 + 1
            date = f"{year}-{month_number:02d}-20"
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


def test_pb_materialization_bounds_history_and_consumer_uses_same_rank() -> None:
    from a_stock_tracker.scoring import validated_pb_percentile

    rows = [
        {
            "trade_date": f"{year}{month:02d}28",
            "pb": float(year - 2010),
            "source": "tushare.daily_basic",
            "source_as_of": "2026-09-30",
        }
        for year in range(2011, 2027)
        for month in range(1, 13)
    ]
    patch = tpm._valuation_patch(rows, "2026-09-30")
    assert patch["valuation_valid_months"] == 120
    assert patch["valuation_window_start"] == "20161028"
    assert patch["valuation_window_end"] == "20260928"
    assert patch["valuation_coverage_status"] == "FULL_10Y"
    assert validated_pb_percentile(patch, "2026-09-30") == patch["pb_percentile_10y"]
    assert patch["pb_percentile_10y"] is not None
    gap = tpm._valuation_patch([row for row in rows if row["trade_date"] != "20200128"], "2026-09-30")
    assert gap["valuation_valid_months"] == 119
    assert gap["valuation_coverage_status"] == "INSUFFICIENT_HISTORY"
    assert gap["pb_percentile_10y"] is None
    short = tpm._valuation_patch(rows[-60:], "2026-09-30")
    assert short["pb_percentile_10y"] is None
    assert validated_pb_percentile(short, "2026-09-30") is None
    # Leap-day cutoffs remain valid and never admit future observations.
    leap = tpm._monthly_valuation(
        [
            {"trade_date": "20140227", "pb": 1},
            {"trade_date": "20140228", "pb": 2},
            {"trade_date": "20240229", "pb": 3},
            {"trade_date": "20240301", "pb": 4},
        ],
        "2024-02-29",
    )
    assert [row["trade_date"] for row in leap] == ["20140228", "20240229"]


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
        run_id = _create_shadow_run(shadow, "dividend", source_as_of="2026-07-14")
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
    assert div["dividend_source_as_of"] == "2026-07-14"


def test_dividend_rejects_negative_dps() -> None:
    row = {
        "div_proc": "实施",
        "ex_date": "20260801",
        "cash_div_tax": -1.0,
        "observed_at": "2026-08-01",
    }

    with pytest.raises(tpm.MaterializationReadinessError, match="INVALID_DPS:20260801"):
        tpm._dividend_patch([row], "2026-08-07")


def test_share_distribution_rate_prefers_aggregate_and_validates_components() -> None:
    assert (
        tpm._share_distribution_rate(
            {"stk_div": 0.2, "stk_bo_rate": None, "stk_co_rate": 0.2, "ex_date": "20260526"},
            "603606",
        )
        == 0.2
    )
    assert tpm._share_distribution_rate(
        {"stk_div": None, "stk_bo_rate": 0.1, "stk_co_rate": 0.2, "ex_date": "20260526"},
        "600036",
    ) == pytest.approx(0.3)
    assert (
        tpm._share_distribution_rate(
            {"stk_div": None, "stk_bo_rate": None, "stk_co_rate": None, "ex_date": "20260526"},
            "600036",
        )
        == 0.0
    )
    with pytest.raises(tpm.MaterializationReadinessError, match="INVALID_SHARE_DISTRIBUTION_RATE:600036:20260526"):
        tpm._share_distribution_rate(
            {"stk_div": 0.2, "stk_bo_rate": 0.1, "stk_co_rate": 0.2, "ex_date": "20260526"},
            "600036",
        )
    for invalid in (-0.1, float("nan"), float("inf")):
        with pytest.raises(
            tpm.MaterializationReadinessError,
            match="INVALID_SHARE_DISTRIBUTION_RATE:600036:20260526",
        ):
            tpm._share_distribution_rate({"stk_div": invalid, "ex_date": "20260526"}, "600036")
        with pytest.raises(
            tpm.MaterializationReadinessError,
            match="INVALID_SHARE_DISTRIBUTION_RATE:600036:20260526",
        ):
            tpm._share_distribution_rate(
                {"stk_div": None, "stk_bo_rate": invalid, "stk_co_rate": 0.0, "ex_date": "20260526"},
                "600036",
            )
    for boundary in (0.0, 1e-12, 10.0):
        assert tpm._share_distribution_rate(
            {"stk_div": None, "stk_bo_rate": boundary, "stk_co_rate": None, "ex_date": "20260526"},
            "600036",
        ) == pytest.approx(boundary)


def test_bps_basis_accumulates_only_post_report_events_and_dedupes_revisions() -> None:
    financial_rows = [
        {
            "bps": 12.368,
            "end_date": "20260331",
            "ann_date": "20260422",
            "observed_at": "2026-04-22T10:00:00",
            "payload_sha256": "financial",
        }
    ]
    dividend_rows = [
        # Before the financial report period: ignored.
        {
            "code": "603606",
            "end_date": "2025-12-31",
            "record_date": "2026-03-01",
            "ex_date": "20260302",
            "div_proc": "实施",
            "stk_div": 0.5,
            "observed_at": "2026-03-02T10:00:00",
            "ann_date": "20260201",
            "payload_sha256": "old",
        },
        # Older revision of the same implemented action: deduped away.
        {
            "code": "603606",
            "end_date": "2025-12-31",
            "record_date": "2026-05-25",
            "ex_date": "20260526",
            "div_proc": "实施",
            "stk_div": 0.1,
            "observed_at": "2026-05-25T09:00:00",
            "ann_date": "20260420",
            "payload_sha256": "revision-a",
        },
        {
            "code": "603606",
            "end_date": "2025-12-31",
            "record_date": "2026-05-25",
            "ex_date": "20260526",
            "div_proc": "实施",
            "stk_div": 0.2,
            "stk_co_rate": 0.2,
            "observed_at": "2026-05-25T10:00:00",
            "ann_date": "20260422",
            "payload_sha256": "revision-b",
        },
        {
            "code": "603606",
            "end_date": "2026-06-30",
            "record_date": "2026-06-29",
            "ex_date": "20260630",
            "div_proc": "实施",
            "stk_div": 0.1,
            "observed_at": "2026-06-29T10:00:00",
            "ann_date": "20260601",
            "payload_sha256": "second",
        },
        # Future event: ignored.
        {
            "code": "603606",
            "end_date": "2026-12-31",
            "record_date": "2026-08-01",
            "ex_date": "20260802",
            "div_proc": "实施",
            "stk_div": 0.3,
            "observed_at": "2026-07-01T10:00:00",
            "ann_date": "20260701",
            "payload_sha256": "future",
        },
    ]

    patch = tpm._bps_basis_patch(financial_rows, dividend_rows, "2026-07-31", "603606")

    assert patch["bps_reported"] == 12.368
    assert patch["bps_share_adjustment_factor"] == pytest.approx(1.32)
    assert patch["bps"] == pytest.approx(12.368 / 1.32)
    assert patch["bps_adjustment_ex_dates"] == ["2026-05-26", "2026-06-30"]
    assert patch["bps_basis_report_period"] == "20260331"
    assert patch["bps_basis_as_of"] == "2026-07-31"


def test_bps_basis_fails_closed_when_cumulative_factor_overflows() -> None:
    financial_rows = [{"bps": 12.368, "end_date": "20260331", "ann_date": "20260422"}]
    dividend_rows = [
        {
            "code": "603606",
            "end_date": "20251231",
            "record_date": "20260525",
            "ex_date": "20260526",
            "div_proc": "实施",
            "stk_div": 1e308,
        },
        {
            "code": "603606",
            "end_date": "20251231",
            "record_date": "20260629",
            "ex_date": "20260630",
            "div_proc": "实施",
            "stk_div": 1e308,
        },
    ]

    with pytest.raises(
        tpm.MaterializationReadinessError,
        match="INVALID_SHARE_DISTRIBUTION_RATE:603606:20260630",
    ):
        tpm._bps_basis_patch(financial_rows, dividend_rows, "2026-07-31", "603606")


def test_valuation_only_and_financial_only_materialize_same_bps_basis(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        financial_run = _create_shadow_run(shadow, "fina_indicator", source_as_of="2026-07-31")
        dividend_run = _create_shadow_run(shadow, "dividend", source_as_of="2026-07-31")
        valuation_run = _create_shadow_run(shadow, "daily_basic", source_as_of="2026-07-31")
        _insert_fina_indicator(
            conn,
            financial_run,
            "600036",
            "2026-04-22",
            "20260331",
            12.0,
            8.0,
            40.0,
            28.0,
            "tushare.fina_indicator",
            "2026-07-31",
            "2026-04-22T10:00:00",
            bps=12.368,
        )
        _insert_dividend(
            conn,
            dividend_run,
            "600036",
            "2026-04-22",
            "2025-12-31",
            "2026-05-25",
            "2026-05-26",
            "实施",
            0.56,
            "tushare.dividend",
            "2026-07-31",
            "2026-05-25T10:00:00",
            stk_div=0.2,
            stk_co_rate=0.2,
        )
        _insert_valuation(
            conn,
            valuation_run,
            "600036",
            "2026-07-31",
            41.15,
            20.0,
            3.9904,
            1000.0,
            800.0,
            1.0,
            "tushare.daily_basic",
            "2026-07-31",
            "2026-07-31T17:00:00",
        )
        conn.commit()

    valuation_only = tpm.build_stock_fundamentals_payload(
        shadow_db_path=shadow,
        as_of_date="2026-07-31",
        watchlist=("600036",),
        execute=False,
        target_db_path=tmp_path / "tracker.db",
        valuation_enabled=True,
        financial_enabled=False,
        dividend_enabled=False,
    )
    financial_only = tpm.build_stock_fundamentals_payload(
        shadow_db_path=shadow,
        as_of_date="2026-07-31",
        watchlist=("600036",),
        execute=False,
        target_db_path=tmp_path / "tracker.db",
        valuation_enabled=False,
        financial_enabled=True,
        dividend_enabled=False,
    )

    expected = pytest.approx(12.368 / 1.2)
    assert valuation_only["600036"]["valuation_basis"]["bps"] == expected
    assert financial_only["600036"]["valuation_basis"]["bps"] == expected


def test_invalid_share_distribution_aborts_all_watchlist_writes(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    tracker = tmp_path / "tracker.db"
    _build_tracker_db(tracker, ("600036", "603606"))
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        financial_run = _create_shadow_run(shadow, "fina_indicator", source_as_of="2026-07-31")
        dividend_run = _create_shadow_run(shadow, "dividend", source_as_of="2026-07-31")
        for code in ("600036", "603606"):
            _insert_fina_indicator(
                conn,
                financial_run,
                code,
                "2026-04-22",
                "20260331",
                12.0,
                8.0,
                40.0,
                28.0,
                "tushare.fina_indicator",
                "2026-07-31",
                "2026-04-22T10:00:00",
                bps=12.368,
            )
        _insert_dividend(
            conn,
            dividend_run,
            "603606",
            "2026-04-22",
            "2025-12-31",
            "2026-05-25",
            "2026-05-26",
            "实施",
            0.56,
            "tushare.dividend",
            "2026-07-31",
            "2026-05-25T10:00:00",
            stk_div=-0.2,
        )
        conn.commit()
    before = tracker.read_bytes()

    with pytest.raises(
        tpm.MaterializationReadinessError,
        match="INVALID_SHARE_DISTRIBUTION_RATE:603606:20260526",
    ):
        tpm.run_materialization(
            shadow_db_path=shadow,
            as_of_date="2026-07-31",
            target_db_path=tracker,
            execute=True,
            financial_enabled=True,
            valuation_enabled=False,
            dividend_enabled=False,
            watchlist=("600036", "603606"),
        )

    assert tracker.read_bytes() == before


def test_preview_fails_closed_when_valuation_basis_is_missing(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    tracker = tmp_path / "tracker.db"
    _build_tracker_db(tracker)
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "daily_basic", source_as_of="2026-07-31")
        _insert_valuation(
            conn,
            run_id,
            "600036",
            "2026-07-31",
            25.0,
            10.0,
            2.0,
            1000.0,
            500.0,
            2.5,
            "tushare.daily_basic",
            "2026-07-31",
            "2026-07-31T17:00:00",
        )
        conn.commit()

    with pytest.raises(tpm.MaterializationReadinessError, match="VALUATION_NOT_READY:600036"):
        tpm.run_materialization(
            shadow_db_path=shadow,
            as_of_date="2026-07-31",
            target_db_path=tracker,
            execute=False,
            valuation_enabled=True,
            financial_enabled=False,
            dividend_enabled=False,
            watchlist=("600036",),
        )


def test_preview_does_not_write_target_db_and_shows_patch(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    tracker = tmp_path / "tracker.db"
    _build_tracker_db(tracker)

    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "daily_basic")
        financial_run = _create_shadow_run(shadow, "fina_indicator")
        _insert_fina_indicator(
            conn,
            financial_run,
            "600036",
            "2026-04-22",
            "20260331",
            12.0,
            8.0,
            40.0,
            28.0,
            "tushare.fina_indicator",
            "2026-07-21",
            "2026-04-22T10:00:00",
            bps=12.368,
        )
        _insert_valuation(
            conn,
            run_id,
            "600036",
            "2026-07-21",
            25.0,
            10.0,
            2.0,
            1000.0,
            500.0,
            2.5,
            "tushare.daily_basic",
            "2026-07-21",
            "2026-07-21T00:00:00",
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


def test_execute_fails_with_empty_watchlist(tmp_path: Path) -> None:
    with pytest.raises(tpm.MaterializationReadinessError):
        tpm.run_materialization(
            shadow_db_path=tmp_path / "shadow.db",
            as_of_date="2026-07-21",
            target_db_path=tmp_path / "tracker.db",
            execute=True,
            valuation_enabled=True,
            watchlist=[],
        )


def test_execute_fails_when_valuation_market_cap_ratio_is_missing(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    tracker = tmp_path / "tracker.db"
    _build_tracker_db(tracker)
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "daily_basic")
        _insert_valuation(
            conn,
            run_id,
            "600036",
            "2026-07-21",
            close=25.0,
            pe_ttm=10.0,
            pb=2.0,
            total_mv=0.0,
            circ_mv=500.0,
            dv_ttm=2.5,
            source="tushare.daily_basic",
            source_as_of="2026-07-21",
            observed_at="2026-07-21T00:00:00",
        )
        conn.commit()

    with pytest.raises(
        tpm.MaterializationReadinessError,
        match="^VALUATION_NOT_READY:600036$",
    ):
        tpm.run_materialization(
            shadow_db_path=shadow,
            as_of_date="2026-07-21",
            target_db_path=tracker,
            execute=True,
            valuation_enabled=True,
            watchlist=("600036",),
        )


def test_execute_accepts_valuation_with_market_cap_ratio(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.db"
    tracker = tmp_path / "tracker.db"
    _build_tracker_db(tracker)
    with tushare_primary_cache.open_shadow_store(shadow) as conn:
        run_id = _create_shadow_run(shadow, "daily_basic")
        financial_run = _create_shadow_run(shadow, "fina_indicator")
        _insert_fina_indicator(
            conn,
            financial_run,
            "600036",
            "2026-04-22",
            "20260331",
            roe_waa=12.0,
            netprofit_yoy=8.0,
            debt_to_assets=40.0,
            grossprofit_margin=28.0,
            source="tushare.fina_indicator",
            source_as_of="2026-07-21",
            observed_at="2026-04-22T10:00:00",
            bps=12.368,
        )
        _insert_valuation(
            conn,
            run_id,
            "600036",
            "2026-07-21",
            close=25.0,
            pe_ttm=10.0,
            pb=2.0,
            total_mv=1000.0,
            circ_mv=500.0,
            dv_ttm=2.5,
            source="tushare.daily_basic",
            source_as_of="2026-07-21",
            observed_at="2026-07-21T00:00:00",
        )
        conn.commit()

    result = tpm.run_materialization(
        shadow_db_path=shadow,
        as_of_date="2026-07-21",
        target_db_path=tracker,
        execute=True,
        valuation_enabled=True,
        watchlist=("600036",),
    )

    assert result.changed_count == 1
    with sqlite3.connect(tracker) as conn:
        row = conn.execute("SELECT data FROM stock_fundamentals WHERE code='600036'").fetchone()
        assert row is not None
        assert json.loads(row[0])["float_to_total_ratio"] == 50.0


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
