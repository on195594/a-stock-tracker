"""Source selection and bounded patch regressions; all evidence is synthetic."""

import copy
import json
from types import SimpleNamespace

import pytest

from a_stock_tracker import automation as a
from a_stock_tracker import disclosures as d
from a_stock_tracker.research import ScreenError
from tests.test_automation import research_case


@pytest.fixture
def selected(tmp_path):
    frozen, state, summary, packet, report, review = research_case(tmp_path)
    packet["citation_format"] = d.SOURCE_FORMAT
    packet["documents"][0]["sha256"] = "a" * 64
    raw = copy.deepcopy(report)
    raw["facts"] = [
        {
            "id": f["id"],
            "claim": f["claim"],
            "source": {
                "kind": "pdf",
                "document": "123",
                "page": 1,
                "start": i,
                "end": i,
            },
        }
        for i, f in enumerate(report["facts"], 1)
    ]
    a.save_state(
        tmp_path / "evidence-600001.SH.json", {"freeze": state["freeze"], "packet": packet}
    )
    return frozen, state, summary, packet, raw, review


def execute(tmp_path, selected):
    frozen, state, summary, *_ = selected
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 300), 5)
    path = tmp_path / "report-600001.SH.json"
    return a.read_state(path)["result"] if path.exists() else None


def test_original_source_round_trip_and_no_generated_quote(selected):
    packet, raw = selected[3:5]
    original = copy.deepcopy(packet)
    numbered = d.model_evidence(packet)
    numbered_text = numbered["documents"][0]["pages"][0]["numbered_text"]
    assert (
        "".join(line.split("|", 1)[1] for line in numbered_text.splitlines(keepends=True))
        == packet["documents"][0]["pages"][0]["text"]
    )
    report = d.source_report(raw, packet)
    assert report["facts"][0]["quote"] == raw["facts"][0]["claim"] + "\n"
    assert packet == original
    raw["facts"][0]["quote"] = "模型生成的引文不允许进入报告"
    with pytest.raises(ScreenError):
        d.source_report(raw, packet)


@pytest.mark.parametrize(
    "change",
    [
        {"start": 0},
        {"end": 999},
        {"start": True},
        {"page": 2},
        {"document": "other"},
        {"kind": "market"},
        {"start": 3, "end": 2},
    ],
)
def test_invalid_source_never_materializes(selected, change):
    packet, raw = selected[3:5]
    raw["facts"][0]["source"].update(change)
    report = d.source_report(raw, packet)
    assert report["facts"][0]["quote"] == ""
    assert d.citation_errors(report, packet)["F1"] == "citation_length"


def test_market_is_not_a_pdf_fact_and_requires_snapshot_date(selected):
    packet, raw = selected[3:5]
    raw["facts"][0]["id"] = "MKT"
    assert d.source_report(raw, packet)["facts"][0]["quote"] == ""
    raw["facts"][0]["id"] = "F1"
    report = d.source_report(raw, packet)
    report["analysis"]["valuation"] = {"text": "行情估值需谨慎。", "facts": ["MKT"]}
    packet["structured"]["market_snapshot"] = {"close": 12.5}
    with pytest.raises(ScreenError):
        d.check_structure(report, packet)
    packet["structured"]["market_snapshot"]["date"] = "2026-99-99"
    with pytest.raises(ScreenError):
        d.check_structure(report, packet)
    packet["structured"]["market_snapshot"]["date"] = "2026-10-16"
    d.check_structure(report, packet)


def test_dates_units_and_numbers_cannot_borrow_unselected_context(selected):
    packet, raw = selected[3:5]
    packet["documents"][0]["period"] = "2026-06-30"
    packet["documents"][0]["pages"][0]["text"] = (
        "单位：万元；本期期末数、上年期末数、变动比例（%）\n"
        "应收账款 100.00 80.00 25.00\n"
        "另一张表：2026年6月末，利润为999.00亿元。\n"
    )
    raw["facts"] = [
        {
            "id": "F1",
            "source": {"kind": "pdf", "document": "123", "page": 1, "start": 1, "end": 2},
            "claim": "本期期末应收账款100.00万元，变动比例25.00%。",
        }
    ]
    report = d.source_report(raw, packet)
    assert not d.citation_errors(report, packet)
    report["facts"][0]["claim"] = "2026年6月末应收账款100.00万元。"
    assert d.citation_errors(report, packet)["F1"] == "numeric_mismatch"
    detail = d.citation_details(report, packet, d.citation_errors(report, packet))[0]
    assert set(detail["detail"]["numbers_not_in_quote"]) == {"2026", "6"}
    assert detail["detail"]["selected_quote"] == report["facts"][0]["quote"]
    assert "另一张表" not in detail["detail"]["selected_quote"]
    report["analysis"] = {"business": {"text": "利润为999.00亿元。", "facts": ["F1"]}}
    assert "business" in d.analysis_number_errors(report, packet)
    report["analysis"]["business"]["text"] = "2026年6月末应收账款100.00万元。"
    assert "business" in d.analysis_number_errors(report, packet)
    raw["facts"][0]["source"]["start"] = 2
    assert d.citation_errors(d.source_report(raw, packet), packet)["F1"] == "numeric_mismatch"


def test_local_patch_rejects_frozen_objects_and_updates_dependencies(selected):
    packet, raw = selected[3:5]
    report = d.source_report(raw, packet)
    targets = d.repair_targets(report, [{"id": "F1", "reason": "numeric_mismatch"}])
    assert set(targets["analysis"]) == set(report["analysis"])
    for patch in (
        {"facts": {"F3": None}, "analysis": {}},
        {"facts": {}, "analysis": {}, "gaps": []},
        {"facts": {"F1": {**raw["facts"][0], "id": "F9"}}, "analysis": {}},
    ):
        with pytest.raises(ScreenError, match="frozen"):
            d.apply_source_patch(raw, patch, targets)
    original = copy.deepcopy(raw)
    merged = d.apply_source_patch(raw, {"facts": {"F1": None}, "analysis": {}}, targets)
    assert raw == original
    with pytest.raises(ScreenError):
        d.check_structure(d.source_report(merged, packet), packet)


@pytest.mark.parametrize("repair", ["valid", "out_of_scope", "still_invalid", "review_failed"])
def test_new_source_flow_repair_and_review(tmp_path, monkeypatch, selected, repair):
    raw, review = selected[4:]
    good = copy.deepcopy(raw["facts"][0])
    raw["facts"][0]["source"]["start"] = 0
    calls = []

    def model(directory, label, prompt, identity, state, budget):
        calls.append(label)
        state["model_calls"] += 1
        if label.endswith("generate"):
            payload = json.loads(prompt.removeprefix(d.SOURCE_GENERATE))
            assert payload["documents"][0]["pages"][0]["numbered_text"].startswith("1|")
            return copy.deepcopy(raw)
        if label.endswith("repair"):
            payload = json.loads(prompt.removeprefix(d.SOURCE_REPAIR))
            assert payload["targets"]["facts"] == ["F1"]
            if repair == "out_of_scope":
                return {"facts": {"F3": None}, "analysis": {}}
            return {
                "facts": {"F1": good if repair in ("valid", "review_failed") else raw["facts"][0]},
                "analysis": {},
            }
        final = json.loads(prompt.removeprefix(d.VERIFY))["report"]
        d.validate_report(final, selected[3])
        return {
            **review,
            "analysis_supported": repair != "review_failed",
            "issues": ["合成：所选原文不支持结论"] if repair == "review_failed" else [],
            "facts": [
                {"id": f["id"], "supported": True, "reason": "synthetic"} for f in final["facts"]
            ],
        }

    monkeypatch.setattr(d, "model_call", model)
    result = execute(tmp_path, selected)
    assert len(calls) == 3
    if repair == "review_failed":
        assert result["evidence_review"] == "failed"
        assert d.structured_report(result)["analysis"] == {}
        return
    assert result["evidence_review"] == "passed"
    assert result["quality"]["repair"] == ("failed" if repair == "out_of_scope" else "repaired")
    assert len(result["report"]["facts"]) == (4 if repair == "valid" else 3)


def test_nine_facts_can_only_delete_good_evidence(tmp_path, monkeypatch, selected):
    raw, review = selected[4:]
    for i in range(5, 10):
        raw["facts"].append({**copy.deepcopy(raw["facts"][-1]), "id": f"F{i}"})
    calls = []

    def model(directory, label, prompt, identity, state, budget):
        calls.append(label)
        state["model_calls"] += 1
        if label.endswith("generate"):
            return raw
        if label.endswith("repair"):
            payload = json.loads(prompt.removeprefix(d.SOURCE_REPAIR))
            targets = payload["targets"]
            assert len(targets["remove_only"]) == 9
            with pytest.raises(ScreenError):
                d.apply_source_patch(
                    raw,
                    {"facts": {"F1": {**raw["facts"][0], "claim": "改写"}}, "analysis": {}},
                    targets,
                )
            return {"facts": {f"F{i}": None for i in range(5, 10)}, "analysis": {}}
        return review

    monkeypatch.setattr(d, "model_call", model)
    result = execute(tmp_path, selected)
    assert result["evidence_review"] == "passed"
    assert len(calls) == 3
    assert len(result["quality"]["dropped"]) == 5


def test_source_repair_replay_does_not_charge_twice(tmp_path, monkeypatch, selected):
    raw, review = selected[4:]
    good = copy.deepcopy(raw["facts"][0])
    raw["facts"][0]["source"]["start"] = 0
    monkeypatch.setattr(d, "model_command", lambda: (["synthetic"], tmp_path))
    calls = []
    interrupted = False
    original = d.model_call

    def model(*args):
        nonlocal interrupted
        if args[1].endswith("verify") and not interrupted:
            interrupted = True
            raise a.BudgetStop("TIME_BUDGET")
        return original(*args)

    def command(args, **kwargs):
        path = args[-1]
        calls.append(path)
        response = (
            raw
            if "-generate." in path
            else ({"facts": {"F1": good}, "analysis": {}} if "-repair." in path else review)
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "tool_count": 0,
                    "provider": "p",
                    "model": "m",
                    "text": json.dumps(response),
                    "usage": {},
                }
            ),
        )

    monkeypatch.setattr(d, "model_call", model)
    monkeypatch.setattr(d, "run_bounded", command)
    assert execute(tmp_path, selected) is None
    result = execute(tmp_path, selected)
    assert result["evidence_review"] == "passed"
    assert len(calls) == selected[1]["model_calls"] == 3


def test_selected_source_format_changes_cache_identity(selected, monkeypatch):
    packet = selected[3]
    identity = selected[1]["model"]
    initial = d.stable_evidence(packet, identity)
    old = copy.deepcopy(packet)
    old.pop("citation_format")
    assert d.stable_evidence(old, identity) != initial
    monkeypatch.setattr(d, "SOURCE_REPAIR", d.SOURCE_REPAIR + "合成修订")
    assert d.stable_evidence(packet, identity) != initial


def test_candidate_rank_rewards_required_coverage(selected):
    report = d.source_report(selected[4], selected[3])
    first = copy.deepcopy(report)
    second = copy.deepcopy(report)
    first["analysis"]["business"]["facts"] = []
    for section in ("summary", "business", "counterevidence"):
        second["analysis"][section]["facts"] = []
    assert d.support_rank(first) > d.support_rank(second)
