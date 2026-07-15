from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from qualitative_v2_audit import (
    QUERIES,
    SAMPLING_SEED,
    SUPER_STRATA,
    SW2021_INDUSTRIES,
    AttemptRecord,
    AuditBlockedError,
    BlobRef,
    FrameRow,
    FrameValidationError,
    PassOverRecord,
    PreregistrationRef,
    SampleEntry,
    SampleManifest,
    SearchResult,
    TechnicalAttemptError,
    build_candidate_corpus,
    build_coverage_report,
    canonical_json_bytes,
    load_preregistration_ref,
    normalize_url,
    select_sample,
    validate_frame,
    wilson_interval,
)


def prereg() -> PreregistrationRef:
    return PreregistrationRef(
        "qualitative-v2-m4-prereg-v1.1", "1.1.0", "protocol.md", "a" * 64, "hashes", SAMPLING_SEED
    )


def frame_rows() -> list[FrameRow]:
    code_by_name = {name: code for code, name in SW2021_INDUSTRIES.items()}
    rows: list[FrameRow] = []
    counter = 0
    for industries in SUPER_STRATA.values():
        industry = industries[0]
        for position in range(6):
            counter += 1
            suffix = "SH" if counter % 2 else "SZ"
            rows.append(
                FrameRow(
                    f"{counter:06d}.{suffix}",
                    f"公司{counter}",
                    code_by_name[industry],
                    industry,
                    Decimal(f"{position + 1}.{counter:06d}"),
                    date(2026, 7, 14),
                    "SSE" if suffix == "SH" else "SZSE",
                )
            )
    return rows


def roomy_frame_rows() -> list[FrameRow]:
    code_by_name = {name: code for code, name in SW2021_INDUSTRIES.items()}
    rows: list[FrameRow] = []
    counter = 100
    for industries in SUPER_STRATA.values():
        industry = industries[0]
        for position in range(8):
            counter += 1
            rows.append(
                FrameRow(
                    f"{counter:06d}.SH",
                    f"公司{counter}",
                    code_by_name[industry],
                    industry,
                    Decimal(position + 1),
                    date(2026, 7, 14),
                    "SSE",
                )
            )
    return rows


def tiny_sample() -> SampleManifest:
    entry = SampleEntry(
        "000001.SH",
        "合成公司",
        "801780.SI",
        "银行",
        "金融地产",
        "low",
        Decimal("1.0000001"),
        "b" * 64,
    )
    return SampleManifest("a" * 64, date(2026, 7, 14), SAMPLING_SEED, (entry,))


def empty_matrix(sample: SampleManifest) -> dict[tuple[str, str, str], list[SearchResult]]:
    matrix: dict[tuple[str, str, str], list[SearchResult]] = {}
    for entry in sample.entries:
        sources = ("CNINFO", "SSE", "CNIPA") if entry.ts_code.endswith(".SH") else ("CNINFO", "SZSE", "CNIPA")
        for source in sources:
            for query in QUERIES:
                matrix[(entry.ts_code, source, query)] = []
    return matrix


def relationship_reports(sample: SampleManifest) -> dict[str, BlobRef]:
    return {
        entry.ts_code: BlobRef(f"relationship-{entry.ts_code}.pdf", "f" * 64, 1, "application/pdf")
        for entry in sample.entries
    }


def test_frozen_mapping_is_exact_partition() -> None:
    names = [name for values in SUPER_STRATA.values() for name in values]
    assert len(SW2021_INDUSTRIES) == len(set(SW2021_INDUSTRIES)) == 31
    assert len(names) == len(set(names)) == 31
    assert set(names) == set(SW2021_INDUSTRIES.values())
    assert all(code.endswith(".SI") for code in SW2021_INDUSTRIES)


def test_v1_and_v11_protocol_hashes_are_frozen() -> None:
    root = Path(__file__).resolve().parents[2]
    old = load_preregistration_ref(
        root / "docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration.md",
        root / "reviews/milestone-004-preregistration/preregistration.sha256",
    )
    current = load_preregistration_ref(
        root / "docs/plans/2026-07-15-milestone-004-evidence-feasibility-preregistration-v1.1.md",
        root / "reviews/milestone-004-preregistration-v1.1/preregistration.sha256",
    )
    assert old.protocol_sha256 == current.supersedes_sha256
    assert current.seed == old.seed == SAMPLING_SEED


def test_frame_decimal_split_and_sample_are_deterministic() -> None:
    frame = validate_frame(reversed(frame_rows()), date(2026, 7, 14), prereg())
    sample = select_sample(frame)
    assert len(sample.entries) == len({entry.ts_code for entry in sample.entries}) == 36
    assert sum(entry.market_cap_stratum == "low" for entry in sample.entries) == 18
    assert sum(entry.market_cap_stratum == "high" for entry in sample.entries) == 18
    for entry in sample.entries:
        expected = hashlib.sha256((SAMPLING_SEED + "\x1f" + entry.ts_code).encode()).hexdigest()
        assert entry.sampling_hash == expected


def test_pre_freeze_pass_over_uses_next_frozen_hash_rank() -> None:
    frame = validate_frame(roomy_frame_rows(), date(2026, 7, 14), prereg())
    baseline = select_sample(frame)
    rejected = baseline.entries[0]
    cell = [
        item
        for item in frame.rows
        if item.super_stratum == rejected.super_stratum and item.market_cap_stratum == rejected.market_cap_stratum
    ]
    ordered = sorted(
        cell,
        key=lambda item: (
            hashlib.sha256((SAMPLING_SEED + "\x1f" + item.row.ts_code).encode()).hexdigest(),
            item.row.ts_code,
        ),
    )
    replacement = next(
        item.row.ts_code for item in ordered if item.row.ts_code not in {entry.ts_code for entry in baseline.entries}
    )
    record = PassOverRecord(
        rejected.ts_code,
        "synthetic_ineligibility",
        "e" * 64,
        datetime(2026, 7, 15, tzinfo=timezone.utc),
        replacement,
        rejected.super_stratum,
        rejected.market_cap_stratum,
        rejected.sampling_hash,
    )
    revised = select_sample(frame, (record,))
    assert rejected.ts_code not in {entry.ts_code for entry in revised.entries}
    assert replacement in {entry.ts_code for entry in revised.entries}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda rows: rows + [rows[0]], "duplicate"),
        (
            lambda rows: [
                FrameRow(row.ts_code, row.name, "999999.SI", row.industry_name, row.total_mv) if not i else row
                for i, row in enumerate(rows)
            ],
            "unknown",
        ),
        (
            lambda rows: [
                FrameRow(row.ts_code, row.name, row.industry_code, "医药生物", row.total_mv) if not i else row
                for i, row in enumerate(rows)
            ],
            "crossed",
        ),
        (
            lambda rows: [
                FrameRow(row.ts_code, row.name, row.industry_code, row.industry_name, Decimal("NaN")) if not i else row
                for i, row in enumerate(rows)
            ],
            "total_mv",
        ),
    ],
)
def test_frame_drift_fails_closed(mutation: object, message: str) -> None:
    with pytest.raises(FrameValidationError, match=message):
        validate_frame(mutation(frame_rows()), date(2026, 7, 14), prereg())  # type: ignore[operator]


@pytest.mark.parametrize(
    ("source", "raw", "expected"),
    [
        (
            "CNINFO",
            "HTTP://User:pw@WWW.CNINFO.COM.CN:80/A/B/../Doc.PDF/?utm_x=1&b=2&a=3#p",
            "https://www.cninfo.com.cn/A/Doc.PDF?a=3&b=2",
        ),
        ("SSE", "https://www.sse.com.cn:443/a/%7e/%2e/b/?spm=x", "https://www.sse.com.cn/a/~/b"),
        (
            "CNIPA",
            "http://pss-system.cponline.cnipa.gov.cn/x?docId=CN%2F123&_ =keep&timestamp=4",
            "https://pss-system.cponline.cnipa.gov.cn/x?_%20=keep&docId=CN%2F123",
        ),
        ("SZSE", "https://WWW.SZSE.CN/", "https://www.szse.cn/"),
    ],
)
def test_source_aware_url_normalization(source: str, raw: str, expected: str) -> None:
    assert normalize_url(source, raw) == expected


def test_url_rejects_nonofficial_or_unsafe_scheme() -> None:
    with pytest.raises(AuditBlockedError):
        normalize_url("CNINFO", "https://cninfo.com.cn.evil.test/doc")
    with pytest.raises(AuditBlockedError):
        normalize_url("CNINFO", "file:///tmp/doc")


def test_matrix_round_robin_and_three_deduplication_rules() -> None:
    sample = tiny_sample()
    matrix = empty_matrix(sample)
    results = [
        SearchResult("000001.SH", "CNINFO", QUERIES[0], 1, "first", "https://cninfo.com.cn/A", "DOC-1", "1" * 64),
        SearchResult(
            "000001.SH", "CNINFO", QUERIES[1], 1, "number duplicate", "https://cninfo.com.cn/B", "DOC-1", "2" * 64
        ),
        SearchResult("000001.SH", "SSE", QUERIES[0], 1, "url first", "https://sse.com.cn/Case/", None, "3" * 64),
        SearchResult("000001.SH", "SSE", QUERIES[1], 1, "url duplicate", "http://SSE.COM.CN:80/Case", None, "4" * 64),
        SearchResult("000001.SH", "CNIPA", QUERIES[0], 1, "hash first", "https://cnipa.gov.cn/C", None, "5" * 64),
        SearchResult("000001.SH", "CNIPA", QUERIES[1], 1, "hash duplicate", "https://cnipa.gov.cn/D", None, "5" * 64),
    ]
    for result in results:
        matrix[(result.ts_code, result.source, result.query)].append(result)
    refs = {
        str(result.raw_sha256): BlobRef(
            f"{result.raw_sha256}.bin", str(result.raw_sha256), 1, "application/octet-stream"
        )
        for result in results
    }
    corpus = build_candidate_corpus(sample, matrix, refs, relationship_reports(sample), ())
    assert [item.title for item in corpus.documents] == ["first", "url first", "hash first"]
    assert [item.matching_rule for item in corpus.duplicates] == ["document_number", "canonical_url", "raw_sha256"]


def test_matrix_requires_explicit_zero_result_groups_and_contiguous_ranks() -> None:
    sample = tiny_sample()
    with pytest.raises(AuditBlockedError, match="every"):
        build_candidate_corpus(sample, {}, {}, relationship_reports(sample), ())
    matrix = empty_matrix(sample)
    result = SearchResult("000001.SH", "CNINFO", QUERIES[0], 2, "bad", "https://cninfo.com.cn/a", None, "1" * 64)
    matrix[(result.ts_code, result.source, result.query)] = [result]
    with pytest.raises(AuditBlockedError, match="contiguous"):
        build_candidate_corpus(sample, matrix, {}, relationship_reports(sample), ())


def test_technical_attempt_ledger_pending_blocked_and_schedule() -> None:
    sample = tiny_sample()
    matrix = empty_matrix(sample)
    base = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    one = (AttemptRecord("download", base, "technical_error"),)
    assert build_candidate_corpus(sample, matrix, {}, {}, one).technical_status == "pending_retry"
    three = tuple(AttemptRecord("download", base.replace(day=10 + offset), "technical_error") for offset in range(3))
    assert build_candidate_corpus(sample, matrix, {}, {}, three).technical_status == "blocked_technical_error"
    bad = (AttemptRecord("download", base, "technical_error"), AttemptRecord("download", base, "technical_error"))
    with pytest.raises(TechnicalAttemptError):
        build_candidate_corpus(sample, matrix, {}, {}, bad)


@pytest.mark.parametrize(
    ("x", "n", "lower", "upper"),
    [
        (0, 6, 0.0, 0.390334287902),
        (3, 6, 0.187616306483, 0.812383693517),
        (4, 6, 0.299993315138, 0.903228588894),
        (6, 6, 0.609665712098, 1.0),
        (0, 18, 0.0, 0.175879223647),
        (11, 18, 0.386190416022, 0.796947534278),
        (12, 18, 0.437494672959, 0.837212252492),
        (18, 18, 0.824120776353, 1.0),
    ],
)
def test_wilson_frozen_binary64_vectors(x: int, n: int, lower: float, upper: float) -> None:
    actual = wilson_interval(x, n)
    assert actual[0] == pytest.approx(lower, abs=5e-13)
    assert actual[1] == pytest.approx(upper, abs=5e-13)
    assert all(isinstance(value, float) for value in actual)


def test_coverage_report_keeps_fail_and_pending_disposition() -> None:
    sample = select_sample(validate_frame(frame_rows(), date(2026, 7, 14), prereg()))
    labels = {entry.ts_code: (index % 2 == 0) for index, entry in enumerate(sample.entries)}
    report = build_coverage_report(sample, labels, {"scope_dispositions": {}})
    assert len(report.rows) == 8
    assert any(not row.passed and row.required_action == "exclude_layer_or_reaudit" for row in report.rows)
    assert report.milestone_005_approval_blocked
    assert all(len(row.wilson_lower_display.split(".")[1]) == 6 for row in report.rows)


def test_canonical_json_is_utf8_compact_and_rejects_nan() -> None:
    assert canonical_json_bytes({"汉": 1, "a": {"z": 2}}) == b'{"a":{"z":2},"\xe6\xb1\x89":1}\n'
    with pytest.raises(ValueError):
        canonical_json_bytes({"bad": float("nan")})
