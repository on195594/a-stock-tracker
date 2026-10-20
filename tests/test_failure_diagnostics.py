"""Offline failure diagnostics preserve evidence without publishing unsafe output."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from a_stock_tracker import automation as a
from a_stock_tracker import disclosures as d
from a_stock_tracker import evidence_pipeline as ep
from a_stock_tracker import opportunities as p
from a_stock_tracker.opportunity_demo import demo_packet
from a_stock_tracker.research import ScreenError
from tests.test_automation import model_response, research_case


@pytest.mark.parametrize("stage", ["select", "compose", "review"])
def test_financial_failure_preserves_evidence_and_hides_draft(tmp_path, monkeypatch, stage):
    frozen, state, summary, packet, report, review = research_case(tmp_path)
    secret = "token=SECRET /private/provider-response draft-private"

    def fail(*_):
        raise ScreenError(secret)

    def model(directory, label, *_):
        if label.endswith("-" + stage):
            fail()
        return model_response(label, report, review)

    monkeypatch.setattr(d, "model_call", model)
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 1800), 5)
    result = a.read_state(tmp_path / "report-600001.SH.json")["result"]
    public = d.structured_report(result)
    assert result["evidence"] == packet
    assert public["diagnostic"]["stage"] == stage
    assert public["diagnostic"]["category"] == "validation_failed"
    assert public["omitted_pages"] == len(packet["omitted_pages"])
    assert public["documents"] == [
        {k: doc[k] for k in ("period", "announced")} for doc in packet["documents"]
    ]
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
    assert "公告元数据" in public["scope"][0]


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
    assert "未知" in d.scope_text(
        {"evidence_review": "passed", "status": "scoped_complete", "omitted_pages": None}
    )


def test_render_passed_report_uses_review_status_with_omitted_pages():
    from tests.test_evidence_integration import envelope
    from tests.test_evidence_pipeline import evidence_case

    result = envelope(evidence_case())
    result["status"] = "unchanged"
    result["evidence"]["omitted_pages"] = [1, 2]
    rendered = d.render(result)
    assert "研究完成" in rendered and "未送入模型的全文页数：2" in rendered
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

    assert "状态：未能生成结论" in rendered
    assert "缺口：原文/模型验证未完成" in rendered


def test_every_pipeline_error_category_has_a_specific_message():
    source = Path(ep.__file__).read_text()
    categories = set(re.findall(r'PipelineError\("[a-z_]+", "([a-z_]+)"\)', source))
    assert categories and categories <= set(d.FAILURE_MESSAGES)
    assert (
        d.failure_diagnostic("repair", ep.PipelineError("repair", "patch_scope"))["category"]
        == "patch_scope"
    )
