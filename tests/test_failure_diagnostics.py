"""Offline failure diagnostics preserve evidence without publishing unsafe output."""

import json
import subprocess

import pytest

from a_stock_tracker import automation as a
from a_stock_tracker import disclosures as d
from a_stock_tracker import opportunities as p
from a_stock_tracker.opportunity_demo import demo_packet
from a_stock_tracker.research import ScreenError
from tests.test_automation import research_case


@pytest.mark.parametrize(
    "stage", ["report_generation", "citation_validation", "independent_review", "review_validation"]
)
def test_financial_failure_preserves_evidence_and_hides_draft(tmp_path, monkeypatch, stage):
    frozen, state, summary, packet, report, review = research_case(tmp_path)
    secret = "token=SECRET /private/provider-response draft-private"

    def fail(*_):
        raise ScreenError(secret)

    def model(directory, label, *_):
        if (label.endswith("generate") and stage == "report_generation") or (
            label.endswith("verify") and stage == "independent_review"
        ):
            fail()
        return review if label.endswith("verify") else report

    monkeypatch.setattr(d, "model_call", model)
    if stage == "citation_validation":
        monkeypatch.setattr(d, "validate_report", fail)
    if stage == "review_validation":
        monkeypatch.setattr(d, "validate_review", fail)
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 300), 5)
    result = a.read_state(tmp_path / "report-600001.SH.json")["result"]
    public = d.structured_report(result)
    assert result["evidence"] == packet
    assert public["diagnostic"]["stage"] == stage
    assert public["diagnostic"]["category"] == "validation_failed"
    assert public["omitted_pages"] == len(packet["omitted_pages"])
    assert public["documents"] == [{"period": "2025-12-31", "announced": "2026-04-30"}]
    assert public["clues"]["pb"] == packet["structured"]["pb"]
    assert public["analysis"] == {} and public["facts"] == []
    assert "report" not in result and "rejected_draft" not in result
    assert secret not in json.dumps(result)
    assert secret not in (tmp_path / "report-600001.SH.md").read_text()


def test_pdf_failure_preserves_only_selected_metadata_with_unknown_coverage(tmp_path, monkeypatch):
    frozen, state, summary, packet, *_ = research_case(tmp_path)
    (tmp_path / "evidence-600001.SH.json").unlink()
    monkeypatch.setattr(d.OfficialSource, "announcements", lambda *_: [])
    doc = {k: packet["documents"][0][k] for k in ("id", "period", "announced")}
    monkeypatch.setattr(d, "select_documents", lambda *_: [doc])

    def fail(*_):
        raise ScreenError("PDF security/report-period identity not established")

    monkeypatch.setattr(d, "extract_document", fail)
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 300), 5)
    public = d.structured_report(a.read_state(tmp_path / "report-600001.SH.json")["result"])
    assert public["diagnostic"]["category"] == "document_identity"
    assert public["diagnostic"]["stage"] == "document_extraction"
    assert public["omitted_pages"] is None
    assert public["documents"] == [{"period": doc["period"], "announced": doc["announced"]}]
    assert "公告元数据" in public["gaps"][0]


@pytest.mark.parametrize(
    "evidence,expected",
    [(None, None), ({}, None), ({"omitted_pages": None}, None), ({"omitted_pages": []}, 0)],
)
def test_old_failure_coverage_is_unknown_without_measurement(evidence, expected):
    result = {"code": "600001.SH", "status": "incomplete", "error": "ScreenError"}
    if evidence is not None:
        result["evidence"] = evidence
    assert d.structured_report(result)["omitted_pages"] == expected


def test_failed_diagnostic_projection_ignores_untrusted_message():
    result = {
        "code": "600001.SH",
        "status": "incomplete",
        "diagnostic": {
            "stage": "document_extraction",
            "category": "document_identity",
            "message": "token=SECRET",
        },
    }
    assert "SECRET" not in json.dumps(d.structured_report(result))
    result["diagnostic"]["stage"] = "private/path"
    assert d.structured_report(result)["diagnostic"] is None
    assert (
        d.failure_diagnostic(
            "document_extraction", subprocess.CalledProcessError(1, "/private/path")
        )["category"]
        == "process_failed"
    )


@pytest.mark.parametrize(
    "kind,category",
    [
        ("missing", "missing_sessions"),
        ("discontinuous", "price_discontinuity"),
        ("numeric", "invalid_number"),
    ],
)
def test_price_failures_have_actionable_reasons_without_levels(tmp_path, kind, category):
    packet = demo_packet()
    if kind == "missing":
        packet["bars"].pop()
    elif kind == "discontinuous":
        packet["bars"][-1]["pre_close"] = 1
    else:
        packet["bars"][-1]["vol"] = 0
    result = p.publish(tmp_path, [packet], a.Budget(20, 300))
    row = result["rows"][0]
    assert row["diagnostic"]["category"] == category
    assert row["diagnostic"]["stage"] == "price_validation"
    assert row["status"] == "gap" and "levels" not in row


@pytest.mark.parametrize(
    "error",
    [
        "token=SECRET /private/path",
        {"stage": "price_collection", "category": "price_identity", "message": "token=SECRET"},
    ],
)
def test_price_collection_error_projection_is_sanitized(tmp_path, error):
    result = p.publish(tmp_path, [{"code": "600001.SH", "collect_error": error}], a.Budget(20, 300))
    assert "SECRET" not in json.dumps(result)
    assert result["rows"][0]["diagnostic"]["stage"] in {"issuer_collection", "price_collection"}


def test_passed_summary_with_unknown_page_count_is_not_complete():
    assert "未知" in d.report_summary_status(
        {"evidence_review": "passed", "status": "scoped_complete", "omitted_pages": None}
    )


def test_render_passed_report_uses_review_status_with_omitted_pages():
    result = {
        "code": "600001.SH",
        "status": "unchanged",
        "evidence_review": "passed",
        "evidence": {
            "documents": [],
            "structured": {
                "industry": "银行",
                "valuation_date": "2026-10-14",
                "pb": 0.8,
                "roe_mean": 10,
            },
            "scope_gaps": [],
            "omitted_pages": [1, 2],
        },
        "report": {
            "facts": [],
            "analysis": {
                field: {"text": "合成判断", "facts": []}
                for field in (
                    "business",
                    "financial",
                    "valuation",
                    "counterevidence",
                    "change_conditions",
                )
            },
            "gaps": [],
        },
    }

    rendered = d.render(result)

    assert "引用已复核" in rendered and "2 页原文未纳入分析" in rendered
    assert "补齐后再研究" not in rendered


def test_render_failed_result_uses_summary_failure_status_and_gap_line():
    result = {
        "code": "600001.SH",
        "status": "incomplete",
        "error": "原文/模型验证未完成",
        "diagnostic": {"stage": "document_extraction", "category": "document_identity"},
        "evidence": {
            "documents": [],
            "structured": {},
            "scope_gaps": ["缺少原文"],
            "omitted_pages": [],
        },
    }

    rendered = d.render(result)

    assert "状态：原文/模型检查失败，没有可读结论" in rendered
    assert "缺口：原文/模型验证未完成" in rendered
