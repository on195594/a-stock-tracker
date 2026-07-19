"""File-isolated orchestration and persistence for qualitative-v2 shadow runs."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Iterator, cast

from qualitative_v2_client import (
    DEFAULT_GEMINI_MODEL,
    FAILURE_REASON_MAX_CHARS,
    GeminiCallResult,
    call_gemini,
)
from qualitative_v2_contract import DIMENSION_NAMES, SCORE_RANGES
from qualitative_v2_types import Evidence, QualitativeContext
from qualitative_v2_validator import validate_context

ARTIFACT_MAX_BYTES = 67_108_864

ShadowClient = Callable[[QualitativeContext, str, str], GeminiCallResult]


@dataclass(frozen=True)
class ShadowRunOutcome:
    """One orchestration result, including whether external work occurred."""

    record: Mapping[str, object]
    api_called: bool
    persisted: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _context_payload(context: QualitativeContext) -> dict[str, object]:
    def evidence_payload(item: Evidence) -> dict[str, object]:
        payload = item.canonical_dict()
        if payload["state_type"] is None:
            payload.pop("state_type")
            payload.pop("effective_until")
        elif payload["state_type"] == "event" and payload["effective_until"] is None:
            payload.pop("effective_until")
        if payload["persistence_horizon"] is None:
            payload.pop("persistence_horizon")
        if payload["materiality"] is None:
            payload.pop("materiality")
        return payload

    evidence = sorted(
        (evidence_payload(item) for item in context.evidence),
        key=_canonical_json,
    )
    return {
        "as_of_date": context.as_of_date,
        "code": context.code,
        "evidence": evidence,
        "industry": context.industry,
        "input_hash": context.compute_input_hash(),
        "name": context.name,
        "rubric_version": context.rubric_version,
        "schema_version": context.schema_version,
        "taxonomy_version": context.taxonomy_version,
    }


def _validate_legacy_scores(legacy_scores: Mapping[str, int] | None) -> dict[str, int] | None:
    if legacy_scores is None:
        return None
    if set(legacy_scores) != set(DIMENSION_NAMES):
        raise ValueError("legacy_scores must contain exactly moat/market_pos/sentiment")
    validated: dict[str, int] = {}
    for dimension in DIMENSION_NAMES:
        score = legacy_scores[dimension]
        minimum, maximum = SCORE_RANGES[dimension]
        if not isinstance(score, int) or isinstance(score, bool) or not minimum <= score <= maximum:
            raise ValueError(f"legacy score {dimension!r} must be an integer in [{minimum},{maximum}]")
        validated[dimension] = score
    return validated


def _comparison_json(
    call_result: GeminiCallResult,
    legacy_scores: dict[str, int] | None,
) -> str | None:
    if legacy_scores is None:
        return None
    v2_scores = {
        dimension: call_result.result.dimension(dimension).score if call_result.result is not None else None
        for dimension in DIMENSION_NAMES
    }
    deltas = {
        dimension: (
            cast(int, v2_scores[dimension]) - legacy_scores[dimension] if v2_scores[dimension] is not None else None
        )
        for dimension in DIMENSION_NAMES
    }
    return _canonical_json(
        {
            "legacy_scores": legacy_scores,
            "score_deltas": deltas,
            "v2_scores": v2_scores,
        }
    )


def _record_key(record: Mapping[str, object]) -> tuple[object, ...]:
    fields = (
        "code",
        "scored_date",
        "schema_version",
        "rubric_version",
        "taxonomy_version",
        "input_hash",
        "model",
    )
    missing = [field for field in fields if field not in record]
    if missing:
        raise ValueError(f"shadow artifact record missing key fields: {missing}")
    return tuple(record[field] for field in fields)


def _secure_flags() -> int:
    return os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)


@contextmanager
def _locked_artifact(path: Path) -> Iterator[IO[str]]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(f"{path.name}.lock")
    lock_fd = os.open(lock_path, _secure_flags(), 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        artifact_fd = os.open(path, _secure_flags(), 0o600)
        try:
            os.fchmod(artifact_fd, 0o600)
            if os.fstat(artifact_fd).st_size > ARTIFACT_MAX_BYTES:
                raise ValueError(f"shadow artifact exceeds {ARTIFACT_MAX_BYTES} bytes; rotate it before appending")
            with os.fdopen(artifact_fd, "r+", encoding="utf-8", closefd=False) as artifact:
                yield artifact
        finally:
            os.close(artifact_fd)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _find_existing(artifact: IO[str], key: tuple[object, ...]) -> dict[str, object] | None:
    artifact.seek(0)
    for line_number, line in enumerate(artifact, start=1):
        if not line.endswith("\n"):
            raise ValueError(f"corrupt JSONL artifact at line {line_number}: missing newline terminator")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupt JSONL artifact at line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"corrupt JSONL artifact at line {line_number}: record is not an object")
        typed_record = cast(dict[str, object], record)
        if _record_key(typed_record) == key:
            return typed_record
    return None


def _append_record(artifact: IO[str], record: Mapping[str, object]) -> None:
    serialized = (_canonical_json(record) + "\n").encode("utf-8")
    if os.fstat(artifact.fileno()).st_size + len(serialized) > ARTIFACT_MAX_BYTES:
        raise ValueError(f"shadow artifact would exceed {ARTIFACT_MAX_BYTES} bytes; rotate it before appending")
    artifact.seek(0, os.SEEK_END)
    artifact.write(serialized.decode("utf-8"))
    artifact.flush()
    os.fsync(artifact.fileno())


def _assert_secret_absent(record: Mapping[str, object], api_key: str) -> None:
    if api_key and api_key in _canonical_json(record):
        raise ValueError("refusing to persist an artifact containing the API key")


def run_shadow_evaluation(
    context: QualitativeContext,
    api_key: str,
    output_path: str | os.PathLike[str],
    *,
    model: str = DEFAULT_GEMINI_MODEL,
    legacy_scores: Mapping[str, int] | None = None,
    client: ShadowClient = call_gemini,
    now: Callable[[], datetime] = _utc_now,
) -> ShadowRunOutcome:
    """Evaluate once per versioned input hash and append an isolated JSONL record.

    The artifact lock is intentionally held across the bounded API call. Shadow
    throughput is low, while preventing duplicate paid calls for the same key
    is more important than parallelism within one artifact file.
    """
    path = Path(output_path)
    if path.suffix.lower() != ".jsonl":
        raise ValueError("shadow output must be a .jsonl artifact, never a database or generic file")
    if not api_key.strip():
        raise ValueError("api_key must be non-empty for an executed shadow run")

    context_validation = validate_context(context)
    if not context_validation.valid or context_validation.context is None:
        raise ValueError(f"invalid shadow context: {context_validation.rejection_reason}")
    context = context_validation.context
    validated_legacy = _validate_legacy_scores(legacy_scores)
    input_hash = context.compute_input_hash()
    key = (
        context.code,
        context.as_of_date,
        context.schema_version,
        context.rubric_version,
        context.taxonomy_version,
        input_hash,
        model,
    )

    with _locked_artifact(path) as artifact:
        existing = _find_existing(artifact, key)
        if existing is not None:
            return ShadowRunOutcome(record=existing, api_called=False, persisted=False)

        call_result = client(context, api_key, model)
        created_at = now()
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("now() must return a timezone-aware datetime")
        failure_reason = (
            call_result.failure_reason[:FAILURE_REASON_MAX_CHARS] if call_result.failure_reason is not None else None
        )
        record: dict[str, object] = {
            "attempts": call_result.attempts,
            "code": context.code,
            "comparison_json": _comparison_json(call_result, validated_legacy),
            "context_json": _canonical_json(_context_payload(context)),
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "failure_reason": failure_reason,
            "input_hash": input_hash,
            "model": model,
            "overall_status": call_result.result.overall_status if call_result.result is not None else None,
            "result_json": _canonical_json(call_result.raw_output) if call_result.raw_output is not None else None,
            "rubric_version": context.rubric_version,
            "schema_version": context.schema_version,
            "scored_date": context.as_of_date,
            "taxonomy_version": context.taxonomy_version,
            "validation_status": call_result.status.value,
        }
        _assert_secret_absent(record, api_key)
        _append_record(artifact, record)
        return ShadowRunOutcome(record=record, api_called=True, persisted=True)


__all__ = ["ShadowRunOutcome", "run_shadow_evaluation"]
