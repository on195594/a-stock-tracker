"""Persistence and orchestration tests for file-isolated v2 shadow runs."""

from __future__ import annotations

import json
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from qualitative_v2_client import GeminiCallResult, ValidationStatus
from qualitative_v2_shadow import ShadowRunOutcome, run_shadow_evaluation
from qualitative_v2_types import DimensionResult, Dimensions, QualitativeContext, ScoringResult


def _context(code: str = "000000") -> QualitativeContext:
    return QualitativeContext(
        code=code,
        name="合同测试",
        industry="synthetic",
        as_of_date="2026-07-15",
        schema_version="qualitative-score-v2",
        rubric_version="rubric-v1",
        taxonomy_version="taxonomy-v1",
        evidence=(),
    )


def _result() -> ScoringResult:
    dimension = DimensionResult(
        status="insufficient_data",
        score=None,
        confidence="low",
        evidence_ids=(),
        rationale="No qualifying evidence was supplied.",
    )
    return ScoringResult(
        schema_version="qualitative-score-v2",
        overall_status="insufficient_data",
        as_of_date="2026-07-15",
        dimensions=Dimensions(moat=dimension, market_pos=dimension, sentiment=dimension),
    )


def _raw_output() -> dict[str, object]:
    result = _result()
    return {
        "schema_version": result.schema_version,
        "overall_status": result.overall_status,
        "as_of_date": result.as_of_date,
        "dimensions": {
            name: {
                "status": result.dimension(name).status,
                "score": result.dimension(name).score,
                "confidence": result.dimension(name).confidence,
                "evidence_ids": list(result.dimension(name).evidence_ids),
                "rationale": result.dimension(name).rationale,
            }
            for name in ("moat", "market_pos", "sentiment")
        },
    }


class _Client:
    def __init__(self, response: GeminiCallResult) -> None:
        self.response = response
        self.calls = 0

    def __call__(self, context: QualitativeContext, api_key: str, model: str) -> GeminiCallResult:
        assert context.code
        assert api_key == "secret"
        assert model == "gemini-2.5-flash"
        self.calls += 1
        return self.response


def _valid_call_result() -> GeminiCallResult:
    return GeminiCallResult(
        status=ValidationStatus.VALID_INSUFFICIENT_DATA,
        result=_result(),
        raw_output=_raw_output(),
        failure_reason=None,
        attempts=1,
    )


def test_shadow_writes_required_jsonl_fields_without_touching_database(tmp_path: Path) -> None:
    output = tmp_path / "shadow.jsonl"
    client = _Client(_valid_call_result())
    now = lambda: datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)

    outcome = run_shadow_evaluation(
        _context(),
        api_key="secret",
        output_path=output,
        client=client,
        now=now,
    )

    assert outcome.api_called
    assert outcome.persisted
    record = json.loads(output.read_text(encoding="utf-8"))
    required = {
        "code",
        "scored_date",
        "schema_version",
        "rubric_version",
        "taxonomy_version",
        "input_hash",
        "model",
        "overall_status",
        "context_json",
        "result_json",
        "validation_status",
        "failure_reason",
        "created_at",
    }
    assert required <= set(record)
    assert record["validation_status"] == "VALID_INSUFFICIENT_DATA"
    assert json.loads(record["context_json"])["code"] == "000000"
    assert json.loads(record["result_json"]) == _raw_output()
    assert "secret" not in output.read_text(encoding="utf-8")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not (tmp_path / "tracker.db").exists()


def test_duplicate_key_skips_redundant_api_call_and_append(tmp_path: Path) -> None:
    output = tmp_path / "shadow.jsonl"
    client = _Client(_valid_call_result())

    first = run_shadow_evaluation(_context(), api_key="secret", output_path=output, client=client)
    second = run_shadow_evaluation(_context(), api_key="secret", output_path=output, client=client)

    assert first.api_called and first.persisted
    assert not second.api_called and not second.persisted
    assert client.calls == 1
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_different_models_have_independent_idempotency_keys(tmp_path: Path) -> None:
    output = tmp_path / "shadow.jsonl"
    called_models: list[str] = []

    def model_client(context: QualitativeContext, api_key: str, model: str) -> GeminiCallResult:
        assert context.code and api_key == "secret"
        called_models.append(model)
        return _valid_call_result()

    first = run_shadow_evaluation(
        _context(),
        api_key="secret",
        output_path=output,
        model="gemini-model-a",
        client=model_client,
    )
    duplicate = run_shadow_evaluation(
        _context(),
        api_key="secret",
        output_path=output,
        model="gemini-model-a",
        client=model_client,
    )
    second_model = run_shadow_evaluation(
        _context(),
        api_key="secret",
        output_path=output,
        model="gemini-model-b",
        client=model_client,
    )

    assert first.api_called and first.persisted
    assert not duplicate.api_called and not duplicate.persisted
    assert second_model.api_called and second_model.persisted
    assert called_models == ["gemini-model-a", "gemini-model-b"]
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [record["model"] for record in records] == ["gemini-model-a", "gemini-model-b"]


def test_different_input_hash_appends_a_new_record(tmp_path: Path) -> None:
    output = tmp_path / "shadow.jsonl"
    client = _Client(_valid_call_result())

    run_shadow_evaluation(_context("000001"), api_key="secret", output_path=output, client=client)
    run_shadow_evaluation(_context("000002"), api_key="secret", output_path=output, client=client)

    assert client.calls == 2
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2


def test_concurrent_duplicate_runs_make_only_one_api_call(tmp_path: Path) -> None:
    output = tmp_path / "shadow.jsonl"
    response = _valid_call_result()
    calls = 0
    calls_lock = threading.Lock()

    def slow_client(context: QualitativeContext, api_key: str, model: str) -> GeminiCallResult:
        nonlocal calls
        assert context.code and api_key and model
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return response

    def run_once() -> ShadowRunOutcome:
        return run_shadow_evaluation(
            _context(),
            api_key="secret",
            output_path=output,
            client=slow_client,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: run_once(), range(2)))

    assert calls == 1
    assert sum(outcome.api_called for outcome in outcomes) == 1
    assert sum(outcome.persisted for outcome in outcomes) == 1
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_failure_is_persisted_with_bounded_reason_and_null_result(tmp_path: Path) -> None:
    output = tmp_path / "shadow.jsonl"
    client = _Client(
        GeminiCallResult(
            status=ValidationStatus.API_RATE_LIMIT,
            result=None,
            raw_output=None,
            failure_reason="RATE_LIMIT_EXHAUSTED: " + "x" * 500,
            attempts=3,
        )
    )

    run_shadow_evaluation(_context(), api_key="secret", output_path=output, client=client)

    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["validation_status"] == "API_RATE_LIMIT"
    assert record["result_json"] is None
    assert record["overall_status"] is None
    assert len(record["failure_reason"]) <= 256


def test_legacy_comparison_is_explicit_and_does_not_change_result(tmp_path: Path) -> None:
    output = tmp_path / "shadow.jsonl"
    client = _Client(_valid_call_result())

    run_shadow_evaluation(
        _context(),
        api_key="secret",
        output_path=output,
        legacy_scores={"moat": 5, "market_pos": 2, "sentiment": 3},
        client=client,
    )

    record = json.loads(output.read_text(encoding="utf-8"))
    comparison = json.loads(record["comparison_json"])
    assert comparison["legacy_scores"] == {"moat": 5, "market_pos": 2, "sentiment": 3}
    assert comparison["v2_scores"] == {"moat": None, "market_pos": None, "sentiment": None}
    assert comparison["score_deltas"] == {"moat": None, "market_pos": None, "sentiment": None}


@pytest.mark.parametrize("name", ["tracker.db", "shadow.txt", "shadow.json"])
def test_output_must_be_a_jsonl_artifact_not_a_database(tmp_path: Path, name: str) -> None:
    client = _Client(_valid_call_result())

    with pytest.raises(ValueError, match=r"\.jsonl"):
        run_shadow_evaluation(_context(), api_key="secret", output_path=tmp_path / name, client=client)

    assert client.calls == 0


def test_corrupt_existing_artifact_fails_before_api_call(tmp_path: Path) -> None:
    output = tmp_path / "shadow.jsonl"
    output.write_text("not-json\n", encoding="utf-8")
    client = _Client(_valid_call_result())

    with pytest.raises(ValueError, match="corrupt JSONL"):
        run_shadow_evaluation(_context(), api_key="secret", output_path=output, client=client)

    assert client.calls == 0
