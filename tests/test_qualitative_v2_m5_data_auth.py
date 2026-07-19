"""Zero-call tests for the bounded M5 data-readiness authorization."""

from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

import a_stock_tracker.qualitative.m5.data_auth as auth
from a_stock_tracker.qualitative.m5.pipeline import _load_sample
from scripts.prepare_qualitative_v2_m5_data_authorization import main as cli_main

SAMPLE = Path(__file__).parent / "fixtures" / "milestone005" / "sample.csv"
AUTHORIZATION_ID = "m5-data-readiness-test-01"
DATA_RUN_ID = "m5-data-test-01"
NOT_BEFORE = "2026-07-19T00:00:00+08:00"
NOT_AFTER = "2026-08-01T23:59:59+08:00"
MATRIX_SHA256 = "46e82996ecb2a6760f4aad31555487f8154ce2459d248182dc06a1d978a81463"
SAMPLE_CODES_SHA256 = "477db2c6ec5b0c929c006e016af3a948d1cd227501929663df35a4421abb3a6f"


def _seal(tmp_path: Path) -> tuple[Path, Path]:
    authorization = tmp_path / "authorization.json"
    checksum = tmp_path / "authorization.sha256"
    auth.seal_data_authorization(
        SAMPLE,
        authorization,
        checksum,
        authorization_id=AUTHORIZATION_ID,
        data_run_id=DATA_RUN_ID,
        not_before=NOT_BEFORE,
        not_after=NOT_AFTER,
    )
    return authorization, checksum


def test_search_matrix_and_authorization_freeze_exact_scale() -> None:
    _sample_sha256, companies = _load_sample(SAMPLE)
    matrix = auth.search_matrix(companies)
    raw = auth._canonical_bytes(matrix)
    value = auth.authorization_value(
        SAMPLE,
        authorization_id=AUTHORIZATION_ID,
        data_run_id=DATA_RUN_ID,
        not_before=NOT_BEFORE,
        not_after=NOT_AFTER,
    )

    assert len(matrix) == value["search_group_count"] == 756
    assert hashlib.sha256(raw).hexdigest() == value["search_matrix_sha256"] == MATRIX_SHA256
    assert value["sample_codes_sha256"] == SAMPLE_CODES_SHA256
    assert value["max_search_http_attempts"] == 2268
    assert value["max_document_http_attempts"] == 2160
    assert value["max_relationship_http_attempts"] == 108
    assert value["max_http_attempts_total"] == 4536
    assert value["attempt_schedule"] == "D_Dplus1_Dplus2_one_attempt_per_local_date"
    assert matrix[0] == {
        "ordinal": 1,
        "code": "002807.SZ",
        "source": "CNINFO",
        "query": "护城河",
        "max_results": 20,
    }
    assert matrix[-1]["ordinal"] == 756
    assert matrix[-1]["source"] == "CNIPA"
    assert matrix[-1]["query"] == "核心技术"


def test_authorization_revalidates_frozen_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "PROTOCOL_SHA256", "0" * 64)
    with pytest.raises(auth.DataAuthorizationError, match="protocol SHA-256 drift"):
        auth.authorization_value(
            SAMPLE,
            authorization_id=AUTHORIZATION_ID,
            data_run_id=DATA_RUN_ID,
            not_before=NOT_BEFORE,
            not_after=NOT_AFTER,
        )


def test_seal_load_and_preflight_are_private_and_zero_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-read")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-read")
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network access"))
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("database access"))
    authorization, checksum = _seal(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}

    result = auth.preflight_data_authorization(
        SAMPLE,
        authorization,
        checksum,
        now=datetime.fromisoformat("2026-07-20T12:00:00+08:00"),
        require_active=True,
    )

    assert result["validated"] is True
    assert result["window_state"] == "active"
    assert result["max_http_attempts_total"] == 4536
    assert result["network_calls_performed"] == 0
    assert result["database_reads_performed"] == 0
    assert result["credential_variables_read"] == 0
    assert result["artifacts_created"] == 0
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert authorization.stat().st_mode & 0o777 == 0o600
    assert checksum.stat().st_mode & 0o777 == 0o600


def test_semantic_tamper_is_rejected_even_with_recomputed_checksum(tmp_path: Path) -> None:
    authorization, checksum = _seal(tmp_path)
    value = cast(dict[str, object], json.loads(authorization.read_text(encoding="utf-8")))
    value["allow_models"] = True
    raw = auth._canonical_bytes(value)
    authorization.write_bytes(raw)
    checksum.write_text(f"{hashlib.sha256(raw).hexdigest()}  {authorization.name}\n", encoding="ascii")

    with pytest.raises(auth.DataAuthorizationError, match="drifts from the frozen boundary"):
        auth.load_data_authorization(SAMPLE, authorization, checksum)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("2026-07-19T00:00:00+08:00", "2026-07-20T23:59:59+08:00", "three calendar dates"),
        ("2026-07-19T00:00:00+08:00", "2026-08-02T00:00:01+08:00", "no longer than 14 days"),
        ("2026-07-19T00:00:00Z", "2026-07-22T00:00:00+08:00", "Asia/Shanghai"),
    ],
)
def test_authorization_window_fails_closed(start: str, end: str, message: str) -> None:
    with pytest.raises(auth.DataAuthorizationError, match=message):
        auth.authorization_value(
            SAMPLE,
            authorization_id=AUTHORIZATION_ID,
            data_run_id=DATA_RUN_ID,
            not_before=start,
            not_after=end,
        )


def test_preflight_reports_and_can_require_window_state(tmp_path: Path) -> None:
    authorization, checksum = _seal(tmp_path)
    pending = auth.preflight_data_authorization(
        SAMPLE,
        authorization,
        checksum,
        now=datetime.fromisoformat("2026-07-18T23:59:59+08:00"),
    )
    expired = auth.preflight_data_authorization(
        SAMPLE,
        authorization,
        checksum,
        now=datetime.fromisoformat("2026-08-02T00:00:00+08:00"),
    )

    assert pending["window_state"] == "not_yet_valid"
    assert expired["window_state"] == "expired"
    with pytest.raises(auth.DataAuthorizationError, match="not_yet_valid"):
        auth.preflight_data_authorization(
            SAMPLE,
            authorization,
            checksum,
            now=datetime.fromisoformat("2026-07-18T23:59:59+08:00"),
            require_active=True,
        )


def test_seal_is_create_only_and_rejects_same_output(tmp_path: Path) -> None:
    authorization, checksum = _seal(tmp_path)
    original = authorization.read_bytes()
    with pytest.raises(auth.DataAuthorizationError, match="already exists"):
        auth.seal_data_authorization(
            SAMPLE,
            authorization,
            checksum,
            authorization_id=AUTHORIZATION_ID,
            data_run_id=DATA_RUN_ID,
            not_before=NOT_BEFORE,
            not_after=NOT_AFTER,
        )
    assert authorization.read_bytes() == original

    same = tmp_path / "same.json"
    with pytest.raises(auth.DataAuthorizationError, match="must differ"):
        auth.seal_data_authorization(
            SAMPLE,
            same,
            same,
            authorization_id=AUTHORIZATION_ID,
            data_run_id="another-run",
            not_before=NOT_BEFORE,
            not_after=NOT_AFTER,
        )


def test_authorization_cli_preview_seal_and_preflight(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    common = [
        "--sample",
        str(SAMPLE),
        "--authorization-id",
        AUTHORIZATION_ID,
        "--data-run-id",
        DATA_RUN_ID,
        "--not-before",
        NOT_BEFORE,
        "--not-after",
        NOT_AFTER,
    ]
    before = set(tmp_path.iterdir())
    assert cli_main(["preview", *common]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["authorization"]["search_matrix_sha256"] == MATRIX_SHA256
    assert set(tmp_path.iterdir()) == before

    authorization = tmp_path / "authorization.json"
    checksum = tmp_path / "authorization.sha256"
    assert cli_main(["seal", *common, "--output", str(authorization), "--checksum-output", str(checksum)]) == 0
    capsys.readouterr()
    assert (
        cli_main(
            [
                "preflight",
                "--sample",
                str(SAMPLE),
                "--authorization",
                str(authorization),
                "--checksum",
                str(checksum),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["validated"] is True
    assert result["network_calls_performed"] == 0
