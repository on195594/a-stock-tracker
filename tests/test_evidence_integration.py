"""New writer, compatible readers and quoted pipeline identity, using synthetic evidence only."""

import copy
import json

import pytest

from a_stock_tracker import automation as a
from a_stock_tracker import disclosures as d
from a_stock_tracker import evidence_blocks as eb
from a_stock_tracker import evidence_pipeline as ep
from a_stock_tracker.contracts import Fact
from a_stock_tracker.mcp_server import Citation
from a_stock_tracker.research import ScreenError
from a_stock_tracker.research_service import ResearchStore, quote, request_info
from a_stock_tracker.web.presenters import quote_lines, report_view
from tests.test_automation import research_case
from tests.test_evidence_pipeline import evidence_case, supported


@pytest.fixture
def case():
    return evidence_case()


def envelope(case):
    packet, report = copy.deepcopy(case)
    packet.update(
        citation_format=eb.VERSION,
        template=eb.REPORT_VERSION,
        pipeline=ep.VERSION,
        scope_gaps=[],
        omitted_pages=[],
    )
    packet["structured"].update(industry="合成", valuation_date="2026-10-19", pb=1, roe_mean=10)
    packet["documents"][0].update(
        title="合成定期报告", announced="2026-08-01", url="https://example.invalid/report.pdf"
    )
    return {
        "code": report["code"],
        "status": "scoped_complete",
        "evidence_review": "passed",
        "evidence": packet,
        "report": report,
        "review": {**supported(report), "report_hash": a.digest(report)},
    }


def test_compatible_reader_preserves_fragments_and_binds_final_review(case):
    result = envelope(case)
    projected = d.structured_report(result)
    assert projected["rule"] == eb.REPORT_VERSION
    table = next(f for f in projected["facts"] if len(f["fragments"]) > 1)
    assert table["quote"] == table["fragments"][0]["text"]
    assert "表头" in {
        f["role_title"] for item in report_view(projected)["facts"] for f in item["fragments"]
    }
    for fact in projected["facts"]:
        Fact.model_validate(fact)
        assert Citation.model_validate({**fact, "page": str(fact["page"])}).fragments
    assert "header · PDF第1页" in d.render(result)
    result["report"]["analysis"]["summary"][0]["text"] += "改动"
    with pytest.raises(ScreenError, match="review binding"):
        d.structured_report(result)


def test_failed_new_report_never_projects_rejected_sentences(case):
    result = envelope(case)
    result["evidence_review"] = "failed"
    result["status"] = "incomplete"
    value = d.structured_report(result)
    assert not value["facts"] and not value["analysis"]


def test_new_packet_and_batch_use_three_stage_path(case, tmp_path, monkeypatch):
    frozen, state, summary, _, _, _ = research_case(tmp_path)
    result = envelope(case)
    # Use the existing synthetic evaluated row, retaining the selected company's indicators.
    old_packet = a.read_state(tmp_path / "evidence-600001.SH.json")["packet"]
    packet = d.evidence_packet(
        result["evidence"]["documents"], old_packet["structured"], ep.VERSION
    )
    assert packet["template"] == eb.REPORT_VERSION and packet["pipeline"] == ep.VERSION
    a.save_state(
        tmp_path / "evidence-600001.SH.json", {"freeze": state["freeze"], "packet": packet}
    )
    state["pipeline"] = ep.VERSION
    stages = []

    def call(directory, label, prompt, model, state, budget):
        stage = label.rsplit("-", 1)[-1]
        stages.append(stage)
        state["model_calls"] += 1
        payload = json.loads(prompt[len(ep.PROMPTS[stage]) :])
        if stage == "select":
            return {k: result["report"][k] for k in ("code", "facts", "gaps")}
        if stage == "compose":
            return {k: result["report"][k] for k in ("code", "analysis")}
        return supported(payload["subject_report"])

    monkeypatch.setattr(d, "model_call", call)
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(100, 1800), 1)
    saved = a.read_state(tmp_path / "report-600001.SH.json")["result"]
    assert stages == ["select", "compose", "review"]
    assert saved["evidence_review"] == "passed"
    assert d.structured_report(saved)["facts"][0]["fragments"]
    assert state["cursor"] == 1


def test_only_evidence_first_pipeline_is_quoted_and_passed_to_child(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCH_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_MODEL", "synthetic")
    monkeypatch.delenv("RESEARCH_PIPELINE", raising=False)
    old = quote(request_info(), "financial")
    monkeypatch.setenv("RESEARCH_PIPELINE", ep.VERSION)
    info = request_info()
    new = quote(info, "financial")
    assert new == old and new["pipeline"] == ep.VERSION
    assert any("正常 3 次" in line for line in quote_lines(info, "financial", new))
    store = ResearchStore(tmp_path / "service")
    command = store.command(tmp_path, {"mode": "financial", "codes": ["600001.SH"], "quote": new})
    assert command[command.index("--pipeline") + 1] == ep.VERSION
    for retired in ("legacy", "unknown"):
        monkeypatch.setenv("RESEARCH_PIPELINE", retired)
        with pytest.raises(ScreenError, match="Unknown configured"):
            request_info()


def test_context_budget_is_quoted_and_child_checks_before_acquisition(monkeypatch, tmp_path):
    import sys

    monkeypatch.setenv("RESEARCH_PIPELINE", ep.VERSION)
    info = request_info()
    offer = quote(info, "financial")
    assert offer["context_budget"] == {"method": "utf8_bytes", "limits": ep.CONTEXT_LIMITS}
    assert any(
        "复核 90 KB" in line and "不是 token" in line
        for line in quote_lines(info, "financial", offer)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "automation",
            "--root",
            str(tmp_path / "batch"),
            "--pipeline",
            ep.VERSION,
            "--context-contract",
            "outdated",
        ],
    )
    monkeypatch.setattr(
        a, "freeze", lambda *args, **kwargs: pytest.fail("must reject before acquisition")
    )
    with pytest.raises(SystemExit) as error:
        a.main()
    assert error.value.code == 2 and not (tmp_path / "batch").exists()


@pytest.mark.parametrize("pipeline", [None, "legacy"])
def test_retired_batch_cannot_resume_or_charge(tmp_path, monkeypatch, pipeline):
    frozen, state, summary, *_ = research_case(tmp_path)
    state["pipeline"] = pipeline
    monkeypatch.setattr(d, "model_call", lambda *args: pytest.fail("retired batch must not charge"))
    before = (tmp_path / "evidence-600001.SH.json").read_bytes()
    with pytest.raises(ScreenError, match="Retired research pipeline"):
        d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(0, 1800), 1)
    assert (tmp_path / "evidence-600001.SH.json").read_bytes() == before


def test_retiring_research_does_not_block_scan_only_batch(tmp_path, monkeypatch):
    frozen, state, summary, *_ = research_case(tmp_path)
    state.pop("pipeline")
    monkeypatch.setattr(d, "model_call", lambda *args: pytest.fail("scan only must not charge"))
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(0, 1800), 0)
    assert state["research"] == {} and state["cursor"] == 0
    assert (tmp_path / "summary.md").is_file()


@pytest.mark.parametrize("old_format", [None, "line-ranges-v1"])
def test_retired_report_reader_refuses_old_formats(case, old_format):
    result = envelope(case)
    result["evidence"]["citation_format"] = old_format
    with pytest.raises(ScreenError, match="Retired report format"):
        d.structured_report(result)
    assert not d.validate_review({}, {"facts": []})
