from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import pytest

from a_stock_tracker.config import WATCHLIST
from a_stock_tracker.data import tushare_primary_batch as batch
from a_stock_tracker.data import tushare_primary_cache as cache
from a_stock_tracker.data import tushare_primary_readiness as readiness


@dataclass(frozen=True)
class FakeSummary:
    status: str
    endpoint: str
    source_as_of: str | None = None
    row_count: int = 0
    record_count: int = 0
    event_count: int = 0
    error_code: str | None = None


def _build_shadow_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = cache.open_shadow_store(path)
    return conn


def _now() -> str:
    return datetime(2026, 7, 21, 15, 0, 0).isoformat()


def _run_request_fingerprint(endpoint: str, code: str) -> str:
    return batch.request_fingerprint(endpoint=endpoint, code=code)


def _write_valuation(
    conn,
    code: str,
    *,
    as_of: str,
    trade_date: str,
    close: float | int | None,
    pb: float | int | None,
    total_mv: float | int | None,
    circ_mv: float | int | None,
    pe_ttm: float | None,
) -> None:
    run_id = cache.create_ingestion_run(
        conn,
        endpoint="daily_basic",
        request_fingerprint=f"valuation:{code}:{as_of}:{trade_date}",
        requested_at=_now(),
    )
    cache.persist_observations(
        conn=conn,
        endpoint="daily_basic",
        rows=[
            {
                "ts_code": f"{code}.SH",
                "trade_date": trade_date,
                "close": close,
                "pe": None,
                "pe_ttm": pe_ttm,
                "pb": pb,
                "ps": 1.0,
                "ps_ttm": 1.0,
                "dv_ratio": 1.0,
                "dv_ttm": 1.0,
                "total_mv": total_mv,
                "circ_mv": circ_mv,
            }
        ],
        run_id=run_id,
        observed_at=_now(),
        pit_status="backfilled_latest",
        source="tushare.daily_basic",
        source_as_of=as_of,
    )
    cache.complete_ingestion_run(
        conn=conn,
        run_id=run_id,
        status="completed",
        completed_at=_now(),
        row_count=1,
        source_as_of=as_of,
        payload_sha256="test",
        raw_artifact_path="test.jsonl.gz",
    )


def _write_financial(
    conn,
    code: str,
    *,
    as_of: str,
    ann_date: str,
    end_date: str,
    f_ann_date: str,
) -> None:
    run_id = cache.create_ingestion_run(
        conn,
        endpoint="fina_indicator",
        request_fingerprint=f"financial:{code}:{as_of}:{ann_date}:{end_date}",
        requested_at=_now(),
    )
    cache.persist_observations(
        conn=conn,
        endpoint="fina_indicator",
        rows=[
            {
                "ts_code": f"{code}.SH",
                "ann_date": ann_date,
                "f_ann_date": f_ann_date,
                "end_date": end_date,
                "report_type": "1",
                "comp_type": "0",
                "end_type": "4",
                "update_flag": "0",
            }
        ],
        run_id=run_id,
        observed_at=_now(),
        pit_status="backfilled_latest",
        source="tushare.fina_indicator",
        source_as_of=as_of,
    )
    cache.complete_ingestion_run(
        conn=conn,
        run_id=run_id,
        status="completed",
        completed_at=_now(),
        row_count=1,
        source_as_of=as_of,
        payload_sha256="test",
        raw_artifact_path="test.jsonl.gz",
    )


def _write_dividend_observed(
    conn,
    code: str,
    *,
    as_of: str,
    ex_date: str | None = "2026-07-18",
    div_proc: str = "实施",
) -> None:
    observation_date = ex_date or "2026-07-18"
    run_id = cache.create_ingestion_run(
        conn,
        endpoint="dividend",
        request_fingerprint=_run_request_fingerprint("dividend", code),
        requested_at=_now(),
    )
    cache.persist_observations(
        conn=conn,
        endpoint="dividend",
        rows=[
            {
                "ts_code": f"{code}.SH",
                "ann_date": observation_date,
                "end_date": observation_date,
                "record_date": observation_date,
                "ex_date": ex_date,
                "div_proc": div_proc,
            }
        ],
        run_id=run_id,
        observed_at=_now(),
        pit_status="backfilled_latest",
        source="tushare.dividend",
        source_as_of=as_of,
    )
    cache.complete_ingestion_run(
        conn=conn,
        run_id=run_id,
        status="completed",
        completed_at=_now(),
        row_count=1,
        source_as_of=as_of,
        payload_sha256="test",
        raw_artifact_path="test.jsonl.gz",
    )


def _write_zero_dividend_run(conn, code: str, *, as_of: str) -> None:
    run_id = cache.create_ingestion_run(
        conn=conn,
        endpoint="dividend",
        request_fingerprint=_run_request_fingerprint("dividend", code),
        requested_at=_now(),
    )
    cache.complete_ingestion_run(
        conn=conn,
        run_id=run_id,
        status="completed",
        completed_at=_now(),
        row_count=0,
        source_as_of=as_of,
        payload_sha256=None,
        raw_artifact_path=None,
    )


def _write_dividend_failed_run(conn, code: str, *, as_of: str) -> None:
    run_id = cache.create_ingestion_run(
        conn=conn,
        endpoint="dividend",
        request_fingerprint=_run_request_fingerprint("dividend", code),
        requested_at=_now(),
    )
    cache.complete_ingestion_run(
        conn=conn,
        run_id=run_id,
        status="failed",
        completed_at=_now(),
        row_count=0,
        source_as_of=as_of,
        error_code="NETWORK_ERROR",
        error_message="timeout",
        payload_sha256=None,
        raw_artifact_path=None,
    )


def test_valuation_readiness_ready_with_valid_rows_for_all_requested_codes(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    watchlist = ["600036", "601288"]
    with _build_shadow_db(db_path) as conn:
        for code in watchlist:
            _write_valuation(
                conn,
                code,
                as_of="2026-07-21",
                trade_date="2026-07-21",
                close=12.3,
                pb=2.4,
                total_mv=1000.0,
                circ_mv=200.0,
                pe_ttm=None,
            )

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="valuation",
        watchlist_codes=watchlist,
    )

    assert report["status"] == "READY"
    assert report["coverage"]["required"] == 2
    assert report["coverage"]["ready"] == 2
    assert report["domains"]["valuation"]["status"] == "READY"
    assert report["domains"]["valuation"]["details"][watchlist[0]]["pe_ttm_status"] == "NOT_APPLICABLE_LOSS"


def test_readiness_holds_empty_watchlist(tmp_path: Path) -> None:
    report = readiness.assess_readiness(
        db_path=tmp_path / "shadow.db",
        as_of="2026-07-21",
        scope="valuation",
        watchlist_codes=[],
    )

    assert report["status"] == "HOLD"
    assert report["reasons"] == ["EMPTY_WATCHLIST"]


def test_valuation_readiness_holds_stale_source_date(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_valuation(
            conn,
            "600036",
            as_of="2026-07-20",
            trade_date="2026-07-20",
            close=12.3,
            pb=2.4,
            total_mv=1000.0,
            circ_mv=200.0,
            pe_ttm=15.0,
        )

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="valuation",
        watchlist_codes=["600036"],
    )

    detail = report["domains"]["valuation"]["details"]["600036"]
    assert report["status"] == "HOLD"
    assert "STALE_VALUATION_DATE" in detail["reasons"]


def test_valuation_readiness_fails_when_required_field_is_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_valuation(
            conn,
            "600036",
            as_of="2026-07-21",
            trade_date="2026-07-21",
            close=0,
            pb=2.4,
            total_mv=1000.0,
            circ_mv=200.0,
            pe_ttm=15.0,
        )

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="valuation",
        watchlist_codes=["600036"],
    )

    assert report["status"] == "HOLD"
    assert report["domains"]["valuation"]["status"] == "HOLD"
    reasons = report["domains"]["valuation"]["details"]["600036"]["reasons"]
    assert any("close" in reason for reason in reasons)


def test_financial_readiness_requires_report_period_and_effective_ann_date(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_financial(
            conn,
            "600036",
            as_of="2026-07-21",
            ann_date="",
            end_date="2025-12-31",
            f_ann_date="",
        )

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="financial",
        watchlist_codes=["600036"],
    )

    assert report["status"] == "HOLD"
    reasons = report["domains"]["financial"]["details"]["600036"]["reasons"]
    assert "MISSING_EFFECTIVE_ANN_DATE" in reasons


def test_financial_readiness_rejects_announcement_after_as_of(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_financial(
            conn,
            "600036",
            as_of="2026-07-21",
            ann_date="2026-08-20",
            end_date="2026-06-30",
            f_ann_date="2026-08-20",
        )

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="financial",
        watchlist_codes=["600036"],
    )

    detail = report["domains"]["financial"]["details"]["600036"]
    assert detail["status"] == "HOLD"
    assert detail["reasons"] == ["FINANCIAL_ANNOUNCEMENT_AFTER_AS_OF"]


def test_financial_readiness_falls_back_to_earlier_pit_valid_period(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_financial(
            conn,
            "600036",
            as_of="2026-07-21",
            ann_date="2026-08-20",
            end_date="2026-06-30",
            f_ann_date="2026-08-20",
        )
        _write_financial(
            conn,
            "600036",
            as_of="2026-07-21",
            ann_date="2026-04-20",
            end_date="2025-12-31",
            f_ann_date="2026-04-20",
        )

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="financial",
        watchlist_codes=["600036"],
    )

    detail = report["domains"]["financial"]["details"]["600036"]
    assert detail == {"status": "READY", "reasons": []}


def test_dividend_business_empty_is_allowed_with_explicit_completed_zero_row_run(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_zero_dividend_run(conn, "603606", as_of="2026-07-21")

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="dividend",
        watchlist_codes=["603606"],
    )

    assert report["status"] == "READY"
    assert report["domains"]["dividend"]["details"]["603606"]["status"] == "BUSINESS_EMPTY"


def test_dividend_fetch_failure_is_not_allowed(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_dividend_failed_run(conn, "603606", as_of="2026-07-21")

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="dividend",
        watchlist_codes=["603606"],
    )

    assert report["status"] == "HOLD"
    reasons = report["domains"]["dividend"]["details"]["603606"]["reasons"]
    assert "DIVIDEND_FETCH_FAILED" in reasons


def test_dividend_readiness_rejects_not_implemented_observation(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_dividend_observed(
            conn,
            "603606",
            as_of="2026-07-21",
            ex_date="2026-07-18",
            div_proc="预案",
        )

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="dividend",
        watchlist_codes=["603606"],
    )

    detail = report["domains"]["dividend"]["details"]["603606"]
    assert detail["status"] == "HOLD"
    assert detail["reasons"] == ["DIVIDEND_NOT_YET_IMPLEMENTED"]


def test_dividend_readiness_rejects_implemented_observation_without_ex_date(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_dividend_observed(
            conn,
            "603606",
            as_of="2026-07-21",
            ex_date=None,
            div_proc="实施",
        )

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="dividend",
        watchlist_codes=["603606"],
    )

    detail = report["domains"]["dividend"]["details"]["603606"]
    assert detail["status"] == "HOLD"
    assert detail["reasons"] == ["DIVIDEND_NOT_YET_IMPLEMENTED"]


def test_dividend_readiness_ignores_orphaned_observation(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        run_id = cache.create_ingestion_run(
            conn,
            endpoint="dividend",
            request_fingerprint=_run_request_fingerprint("dividend", "603606"),
            requested_at=_now(),
        )
        cache.complete_ingestion_run(
            conn=conn,
            run_id=run_id,
            status="completed",
            completed_at=_now(),
            row_count=1,
            source_as_of="2026-07-21",
        )
        conn.execute(
            """INSERT INTO dividend_observations
            (record_key, code, ann_date, end_date, record_date, ex_date, div_proc,
             source, payload_json, payload_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "orphaned-dividend-record",
                "603606",
                "2026-07-18",
                "2026-07-18",
                "2026-07-18",
                "2026-07-18",
                "预案",
                "tushare.dividend",
                "{}",
                "test",
            ),
        )
        conn.commit()

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="dividend",
        watchlist_codes=["603606"],
    )

    detail = report["domains"]["dividend"]["details"]["603606"]
    assert detail["status"] == "HOLD"
    assert detail["reasons"] == ["MISSING_DIVIDEND_OBSERVATION"]


def test_batch_readiness_all_scope_merges_domain_reports_and_counts_watched_codes(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    watchlist = ["600036", "601288", "601939"]
    with _build_shadow_db(db_path) as conn:
        for code in watchlist:
            _write_valuation(
                conn,
                code,
                as_of="2026-07-21",
                trade_date="2026-07-21",
                close=10.0,
                pb=1.1,
                total_mv=1000.0,
                circ_mv=500.0,
                pe_ttm=20.0,
            )
            _write_financial(
                conn,
                code,
                as_of="2026-07-21",
                ann_date="2026-07-20",
                end_date="2025-12-31",
                f_ann_date="2026-07-20",
            )
            _write_dividend_observed(conn, code, as_of="2026-07-21")

    report = readiness.assess_readiness(
        db_path=db_path,
        as_of="2026-07-21",
        scope="all",
        watchlist_codes=watchlist,
    )

    assert report["status"] == "READY"
    assert report["coverage"]["required"] == 3
    assert report["coverage"]["ready"] == 3
    assert report["domains"]["dividend"]["details"]["600036"]["source_as_of"] == "2026-07-21"


def test_readiness_script_outputs_json_and_nonzero_on_hold(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "shadow.db"
    with _build_shadow_db(db_path) as conn:
        _write_dividend_failed_run(conn, "603606", as_of="2026-07-21")

    code = readiness.readiness_main(
        [
            "--db-path",
            str(db_path),
            "--as-of-date",
            "2026-07-21",
            "--scope",
            "dividend",
            "--watchlist-codes",
            "603606",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out["status"] == "HOLD"
    assert out["domains"]["dividend"]["status"] == "HOLD"


def test_readiness_scope_requires_explicit_db_path_and_as_of() -> None:
    with pytest.raises(SystemExit) as exc_info:
        readiness.readiness_main(["--scope", "valuation", "--watchlist-codes", "600036"])
    assert exc_info.value.code == 2


def test_check_readiness_uses_default_35_watchlist_when_not_explicitly_passed(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.db"
    watchlist = [str(item["code"]) for item in WATCHLIST]
    with _build_shadow_db(db_path) as conn:
        for code in watchlist[:2]:
            _write_valuation(
                conn,
                code,
                as_of="2026-07-21",
                trade_date="2026-07-21",
                close=12.3,
                pb=2.4,
                total_mv=1000.0,
                circ_mv=200.0,
                pe_ttm=18.0,
            )

    report = readiness.assess_readiness(
        db_path=db_path, as_of="2026-07-21", scope="valuation", watchlist_codes=watchlist
    )
    assert report["coverage"]["required"] == len(watchlist)
    assert report["coverage"]["ready"] == 2
    # missing 33 codes intentionally keep as HOLD
    assert report["status"] == "HOLD"
    assert report["domains"]["valuation"]["status"] == "HOLD"
