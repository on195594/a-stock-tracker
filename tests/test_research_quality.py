"""Synthetic coverage for bounded citation repair and final-content review."""

import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from a_stock_tracker import automation as a
from a_stock_tracker import disclosures as d
from a_stock_tracker.research import ScreenError
from a_stock_tracker.research_service import result_summary
from tests.test_automation import research_case


@pytest.fixture
def case(tmp_path):
    frozen, state, summary, packet, report, _ = research_case(tmp_path)
    packet["template"] = d.TEMPLATE
    quote = "公司持续开展研发和技术投入，相关支出需要核查其可持续性。"
    packet["documents"][0]["pages"][0]["text"] += quote
    report["facts"].append(
        {"id": "F5", "document": "123", "page": 1, "quote": quote, "claim": quote}
    )
    report["facts"][0]["quote"] += "这不是逐字原文。"
    for section in ("business", "financial", "valuation", "counterevidence"):
        report["analysis"][section]["facts"] = ["F1", "F2"]
    report["analysis"]["change_conditions"]["facts"] = ["F5"]
    a.save_state(
        tmp_path / "evidence-600001.SH.json", {"freeze": state["freeze"], "packet": packet}
    )
    return frozen, state, summary, packet, report


def fixed_report(case):
    report = copy.deepcopy(case[4])
    report["facts"][0]["quote"] = report["facts"][0]["claim"]
    return report


def run_case(tmp_path, case):
    frozen, state, summary, *_ = case
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 300), 5)
    path = tmp_path / "report-600001.SH.json"
    return a.read_state(path)["result"] if path.exists() else None


def fake_model(monkeypatch, case, repaired=None, review_passed=True):
    calls = []
    initial_calls = case[1]["model_calls"]

    def model(directory, label, prompt, identity, state, budget):
        calls.append(label)
        state["model_calls"] += 1
        if label.endswith("-generate"):
            return copy.deepcopy(case[4])
        if label.endswith("-repair"):
            payload = json.loads(prompt.removeprefix(d.REPAIR))
            assert payload["report"] == case[4]
            assert payload["evidence"] == case[3]
            assert payload["errors"] == [{"id": "F1", "reason": "citation_mismatch"}]
            return copy.deepcopy(repaired if repaired is not None else case[4])
        assert label.endswith("-verify")
        payload = json.loads(prompt.removeprefix(d.VERIFY))
        final = payload["report"]
        d.validate_report(final, case[3])
        return {
            "facts": [
                {"id": fact["id"], "supported": True, "reason": "原文支持"}
                for fact in final["facts"]
            ],
            "analysis_supported": review_passed,
            "issues": [] if review_passed else ["合成语义错误"],
        }

    monkeypatch.setattr(d, "model_call", model)
    return calls, initial_calls


def assert_published(result, count):
    assert result["status"] == "scoped_complete"
    assert result["evidence_review"] == "passed"
    public = d.structured_report(result)
    assert len(public["facts"]) == count
    assert public["analysis"] == result["report"]["analysis"]
    assert public["quality"] == result["quality"]
    assert result_summary({"reports": [public]})["reports"][0]["quality"] == result["quality"]
    assert "rejected_draft" not in result


def assert_hidden(result):
    public = d.structured_report(result)
    assert result["status"] == "incomplete"
    assert "report" not in result
    assert public["analysis"] == {} and public["facts"] == []
    assert "rejected_draft" not in public
    assert "假设：现金流持续性需要验证。" not in d.render(result)


def test_repair_success_publishes_reviewed_report(tmp_path, monkeypatch, case):
    calls, initial = fake_model(monkeypatch, case, fixed_report(case))
    result = run_case(tmp_path, case)
    assert_published(result, 5)
    assert result["quality"] == {
        "generated": 5,
        "repair": "repaired",
        "dropped": [],
        "unformed": {},
        "passed": 5,
    }
    assert calls == ["600001.SH-generate", "600001.SH-repair", "600001.SH-verify"]
    assert case[1]["model_calls"] - initial == len(calls) == 3
    assert "质量：修复 1 次" in d.render(result)


def test_still_bad_fact_is_dropped_before_review(tmp_path, monkeypatch, case):
    calls, initial = fake_model(monkeypatch, case)
    original = copy.deepcopy(case[4])
    result = run_case(tmp_path, case)
    assert_published(result, 4)
    assert result["quality"]["repair"] == "repaired"
    assert result["quality"]["dropped"] == [{"id": "F1", "reason": "citation_mismatch"}]
    assert result["quality"]["passed"] == 4
    assert all("F1" not in section["facts"] for section in result["report"]["analysis"].values())
    assert case[4] == original
    assert case[1]["model_calls"] - initial == len(calls) == 3
    assert "质量：修复 1 次 / 剔除 1 条未通过校验的事实" in d.render(result)


@pytest.mark.parametrize("section", ["business", "financial", "counterevidence"])
def test_unsupported_required_section_hides_draft(tmp_path, monkeypatch, case, section):
    case[4]["analysis"][section]["facts"] = ["F1"]
    calls, _ = fake_model(monkeypatch, case)
    result = run_case(tmp_path, case)
    assert_hidden(result)
    assert "rejected_draft" not in result
    assert result["diagnostic"] == {
        "stage": "citation_validation",
        "category": "section_unsupported",
        "message": "业务、财务或最强反证缺少通过校验的原文事实；未发布草稿。",
    }
    assert result["quality"]["passed"] == 0
    assert len(result["quality"]["dropped"]) == 1
    assert not any(label.endswith("-verify") for label in calls)


def test_budget_reserves_verify_and_skips_repair(tmp_path, monkeypatch, case):
    case[1]["model_calls"] = 8
    calls, initial = fake_model(monkeypatch, case)
    result = run_case(tmp_path, case)
    assert_published(result, 4)
    assert result["quality"]["repair"] == "skipped_budget"
    assert result["quality"]["dropped"] == [{"id": "F1", "reason": "citation_mismatch"}]
    assert calls == ["600001.SH-generate", "600001.SH-verify"]
    assert case[1]["model_calls"] - initial == len(calls) == 2
    assert case[1]["model_calls"] == 10
    assert "质量：调用预算不足，未修复 / 剔除 1 条未通过校验的事实" in d.render(result)


def test_final_review_failure_hides_pruned_draft(tmp_path, monkeypatch, case):
    fake_model(monkeypatch, case, review_passed=False)
    result = run_case(tmp_path, case)
    assert_hidden(result)
    assert result["evidence_review"] == "failed"
    assert result["quality"]["passed"] == 4
    assert "F1" not in {fact["id"] for fact in result["rejected_draft"]["facts"]}


@pytest.mark.parametrize("model_unformed", [False, True])
@pytest.mark.parametrize("section", ["valuation", "change_conditions"])
def test_optional_section_placeholder_and_reason(
    tmp_path, monkeypatch, case, model_unformed, section
):
    item = case[4]["analysis"][section]
    item["facts"] = [] if model_unformed else ["F1"]
    if model_unformed:
        item["text"] = "事实不足，未形成"
    fake_model(monkeypatch, case)
    result = run_case(tmp_path, case)
    assert_published(result, 4)
    assert result["report"]["analysis"][section] == {"text": "事实不足，未形成", "facts": []}
    assert result["quality"]["unformed"] == {
        section: "model_marked_insufficient" if model_unformed else "supporting_facts_dropped"
    }
    assert "事实不足，未形成" in d.render(result)
    assert "事实不足，未形成 []" not in d.render(result)


def test_structurally_invalid_repair_falls_back(tmp_path, monkeypatch, case):
    repaired = fixed_report(case)
    repaired["analysis"]["counterevidence"]["facts"] = ["unknown"]
    calls, _ = fake_model(monkeypatch, case, repaired)
    result = run_case(tmp_path, case)
    assert_published(result, 4)
    assert result["quality"]["repair"] == "failed"
    assert result["quality"]["dropped"] == [{"id": "F1", "reason": "citation_mismatch"}]
    assert len(calls) == case[1]["model_calls"] == 3


@pytest.mark.parametrize(
    "error", [ScreenError, ValueError, KeyError, TypeError, subprocess.SubprocessError]
)
def test_repair_call_failure_falls_back(tmp_path, monkeypatch, case, error):
    fake_model(monkeypatch, case)
    original = d.model_call

    def model(*args):
        if args[1].endswith("-repair"):
            args[4]["model_calls"] += 1
            raise error("synthetic")
        return original(*args)

    monkeypatch.setattr(d, "model_call", model)
    result = run_case(tmp_path, case)
    assert_published(result, 4)
    assert result["quality"]["repair"] == "failed"


def test_structural_first_round_never_repairs(tmp_path, monkeypatch, case):
    case[4]["analysis"]["financial"]["facts"] = ["unknown"]
    calls, _ = fake_model(monkeypatch, case)
    result = run_case(tmp_path, case)
    assert_hidden(result)
    assert result["diagnostic"]["stage"] == "citation_validation"
    assert result["quality"] == {
        "generated": 5,
        "repair": "none",
        "dropped": [],
        "unformed": {},
        "passed": 0,
    }
    assert calls == ["600001.SH-generate"]


def test_too_few_verified_facts_fails_with_quality(tmp_path, monkeypatch, case):
    case[4]["facts"] = case[4]["facts"][:3]
    case[4]["analysis"]["change_conditions"]["facts"] = ["F3"]
    fake_model(monkeypatch, case)
    result = run_case(tmp_path, case)
    assert_hidden(result)
    assert result["diagnostic"]["category"] == "insufficient_facts"
    assert result["diagnostic"]["stage"] == "citation_validation"
    assert result["diagnostic"]["message"] == "通过校验的原文事实不足 3 条；未发布草稿。"
    assert result["quality"]["generated"] == 3
    assert result["quality"]["passed"] == 0


def test_failure_before_generation_has_no_quality(tmp_path, monkeypatch, case):
    def model(*_):
        raise ScreenError("synthetic")

    monkeypatch.setattr(d, "model_call", model)
    result = run_case(tmp_path, case)
    assert_hidden(result)
    assert "quality" not in result
    assert d.structured_report(result)["quality"] is None


@pytest.mark.parametrize("initial_calls", [0, 7])
@pytest.mark.parametrize("crash_repair", [False, True])
def test_real_model_call_resume_never_recharges_repair(
    tmp_path, monkeypatch, case, crash_repair, initial_calls
):
    # 7 leaves room for exactly generate, repair and verify; resume must replay the repair.
    case[1]["model_calls"] = initial_calls
    monkeypatch.setattr(d, "model_command", lambda: (["synthetic"], tmp_path))
    original_call = d.model_call
    dispatched = []
    attempts = []
    interrupted = False

    def model(*args):
        nonlocal interrupted
        attempts.append(args[1])
        if args[1].endswith("-verify") and not interrupted:
            interrupted = True
            raise a.BudgetStop("TIME_BUDGET")
        return original_call(*args)

    def command(args, **kwargs):
        path = Path(args[-1])
        dispatched.append(path.name)
        request = json.loads(path.read_text())
        if path.name.endswith("-repair.request.json") and crash_repair:
            raise subprocess.TimeoutExpired("synthetic", 260)
        if path.name.endswith("-verify.request.json"):
            final = json.loads(request["prompt"].removeprefix(d.VERIFY))["report"]
            assert len(final["facts"]) == (4 if crash_repair else 5)
            response = {
                "facts": [
                    {"id": fact["id"], "supported": True, "reason": "原文支持"}
                    for fact in final["facts"]
                ],
                "analysis_supported": True,
                "issues": [],
            }
        else:
            response = fixed_report(case) if "-repair." in path.name else case[4]
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
    assert run_case(tmp_path, case) is None
    assert case[1]["cursor"] == 0
    assert len(dispatched) == case[1]["model_calls"] - initial_calls == 2
    assert (tmp_path / "600001.SH-repair.request.json").exists()
    if crash_repair:
        audit = a.read_state(tmp_path / "600001.SH-repair.usage.json")
        assert audit["status"] == "started" and audit["usage"] is None
        assert not (tmp_path / "600001.SH-repair.response.json").exists()
    result = run_case(tmp_path, case)
    assert_published(result, 4 if crash_repair else 5)
    assert result["quality"]["repair"] == ("failed" if crash_repair else "repaired")
    assert len(dispatched) == case[1]["model_calls"] - initial_calls == 3
    assert sum(label.endswith("-repair") for label in attempts) == 2
    assert sum(label.endswith("-repair.request.json") for label in dispatched) == 1


def test_v1_projection_and_render_keep_rule_identity(case):
    packet = case[3]
    packet["template"] = "cited-machine-research-v1"
    result = {
        "code": case[4]["code"],
        "status": "scoped_complete",
        "evidence_review": "passed",
        "evidence": packet,
        "report": fixed_report(case),
    }
    original = copy.deepcopy(result)
    public = d.structured_report(result)
    assert public["quality"] is None and len(public["facts"]) == 5
    old_projection = dict(public)
    del old_projection["quality"]
    assert result_summary({"reports": [old_projection]})["reports"][0]["quality"] is None
    assert "规则：cited-machine-research-v1" in d.render(result)
    assert "质量：" not in d.render(result)
    assert result == original


def test_repair_prompt_changes_evidence_digest(monkeypatch, case):
    before = d.stable_evidence(case[3], case[1]["model"])
    monkeypatch.setattr(d, "REPAIR", d.REPAIR + "合同更正")
    assert before != d.stable_evidence(case[3], case[1]["model"])


@pytest.mark.parametrize(
    "field,value,reason,message",
    [
        ("quote", "短引文", "citation_length", "Citation length outside"),
        ("document", "missing", "citation_mismatch", "Unbound/nonverbatim citation"),
        ("page", 99, "citation_mismatch", "Unbound/nonverbatim citation"),
        (
            "quote",
            "一段原文没有包含且不应该被通过的伪造引文。",
            "citation_mismatch",
            "Unbound/nonverbatim citation",
        ),
        ("claim", "营业收入999", "numeric_mismatch", "Numeric claim not literal"),
        ("claim", "营业收入亿元", "unit_mismatch", "Numeric unit mismatch"),
    ],
)
def test_citation_reason_codes_preserve_strict_diagnostics(case, field, value, reason, message):
    report = fixed_report(case)
    report["facts"][0][field] = value
    d.check_structure(report, case[3])
    assert d.citation_errors(report, case[3]) == {"F1": reason}
    with pytest.raises(ScreenError, match=message):
        d.validate_report(report, case[3])


@pytest.mark.parametrize(
    "damage",
    [
        "keys",
        "code",
        "facts_type",
        "facts_empty",
        "facts_many",
        "fact_keys",
        "fact_type",
        "page_bool",
        "duplicate",
        "blank_id",
        "sections",
        "section_keys",
        "blank_text",
        "refs_type",
        "unknown_ref",
        "unhashable_ref",
        "required_empty",
        "optional_empty",
        "gaps",
    ],
)
def test_structure_errors_fail_without_citation_repair(case, damage):
    report = fixed_report(case)
    if damage == "keys":
        report["extra"] = True
    elif damage == "code":
        report["code"] = "000001.SZ"
    elif damage == "facts_type":
        report["facts"] = {}
    elif damage == "facts_empty":
        report["facts"] = []
    elif damage == "facts_many":
        report["facts"] *= 2
    elif damage == "fact_keys":
        del report["facts"][0]["quote"]
    elif damage == "fact_type":
        report["facts"][0]["claim"] = None
    elif damage == "page_bool":
        report["facts"][0]["page"] = True
    elif damage == "duplicate":
        report["facts"][0]["id"] = "F2"
    elif damage == "blank_id":
        report["facts"][0]["id"] = " "
    elif damage == "sections":
        del report["analysis"]["business"]
    elif damage == "section_keys":
        report["analysis"]["business"]["extra"] = True
    elif damage == "blank_text":
        report["analysis"]["business"]["text"] = " "
    elif damage == "refs_type":
        report["analysis"]["business"]["facts"] = "F1"
    elif damage == "unknown_ref":
        report["analysis"]["business"]["facts"] = ["unknown"]
    elif damage == "unhashable_ref":
        report["analysis"]["business"]["facts"] = [[]]
    elif damage == "required_empty":
        report["analysis"]["business"]["facts"] = []
    elif damage == "optional_empty":
        report["analysis"]["valuation"]["facts"] = []
    else:
        report["gaps"] = [None]
    with pytest.raises(ScreenError):
        d.check_structure(report, case[3])


def test_clean_three_fact_report_needs_no_repair(tmp_path, monkeypatch, case):
    case[4].update(fixed_report(case))
    case[4]["facts"] = case[4]["facts"][:3]
    case[4]["analysis"]["change_conditions"]["facts"] = ["F3"]
    calls, _ = fake_model(monkeypatch, case)
    result = run_case(tmp_path, case)
    assert_published(result, 3)
    assert result["quality"] == {
        "generated": 3,
        "repair": "none",
        "dropped": [],
        "unformed": {},
        "passed": 3,
    }
    assert calls == ["600001.SH-generate", "600001.SH-verify"]
    assert case[1]["model_calls"] == len(calls) == 2
    assert "质量：" not in d.render(result)


def test_repair_budget_stop_leaves_candidate_resumable(tmp_path, monkeypatch, case):
    fake_model(monkeypatch, case)
    original_call = d.model_call

    def model(*args):
        if args[1].endswith("-repair"):
            raise a.BudgetStop("TIME_BUDGET")
        return original_call(*args)

    monkeypatch.setattr(d, "model_call", model)
    assert run_case(tmp_path, case) is None
    assert case[1]["cursor"] == 0 and case[1]["research"] == {}
    assert not (tmp_path / "report-600001.SH.md").exists()


def test_prune_does_not_mutate_generated_report(case):
    original = copy.deepcopy(case[4])
    final, dropped, unformed = d.prune_report(case[4], d.citation_errors(case[4], case[3]))
    assert case[4] == original
    d.validate_report(final, case[3])
    assert len(final["facts"]) == 4
    assert dropped == [{"id": "F1", "reason": "citation_mismatch"}] and unformed == {}


def test_facts_deleted_by_repair_are_recorded(tmp_path, monkeypatch, case):
    repaired = copy.deepcopy(case[4])
    repaired["facts"] = [fact for fact in repaired["facts"] if fact["id"] != "F1"]
    for item in repaired["analysis"].values():
        item["facts"] = [fid for fid in item["facts"] if fid != "F1"]
    fake_model(monkeypatch, case, repaired)
    result = run_case(tmp_path, case)
    assert_published(result, 4)
    assert result["quality"]["repair"] == "repaired"
    assert result["quality"]["dropped"] == [{"id": "F1", "reason": "removed_by_repair"}]
    assert result["quality"]["passed"] == 4
    assert "剔除 1 条未通过校验的事实" in d.render(result)


@pytest.mark.parametrize("text", ["事实不足，未形成。", " 事实不足，未形成 "])
def test_placeholder_punctuation_is_normalised_not_structural(tmp_path, monkeypatch, case, text):
    report = fixed_report(case)
    report["analysis"]["valuation"] = {"text": text, "facts": []}
    case[4].clear()
    case[4].update(report)
    fake_model(monkeypatch, case)
    result = run_case(tmp_path, case)
    assert_published(result, 5)
    assert result["report"]["analysis"]["valuation"] == {"text": "事实不足，未形成", "facts": []}
    assert result["quality"]["unformed"] == {"valuation": "model_marked_insufficient"}


def test_citation_errors_allow_document_metadata_years(case):
    report = fixed_report(case)
    # The referenced document has period "2025-12-31" and announced "2026-04-18"
    report["facts"][0]["claim"] = "2025年年度财务报告审计意见为有效事实。"
    assert d.citation_errors(report, case[3]) == {}

    # Invented financial amount disguised as a year number must still fail
    report["facts"][0]["claim"] = "营业收入为2025万元。"
    assert d.citation_errors(report, case[3]) == {"F1": "numeric_mismatch"}

    # Even if the claim starts with a valid year, an invented amount sharing the year literal must fail
    report["facts"][0]["claim"] = "2025年营业收入为2025万元。"
    assert d.citation_errors(report, case[3]) == {"F1": "numeric_mismatch"}

    # Date fragments from metadata like month/day (-12, -31) must not be authorized as amounts
    report["facts"][0]["claim"] = "本期净利润为-12万元。"
    assert d.citation_errors(report, case[3]) == {"F1": "numeric_mismatch"}

    # Unauthorized years outside metadata must also fail
    report["facts"][0]["claim"] = "2024年营业收入同比变化。"
    assert d.citation_errors(report, case[3]) == {"F1": "numeric_mismatch"}


def test_scoped_complete_preserved_with_omitted_pages(tmp_path, monkeypatch, case):
    case[3]["omitted_pages"] = [10, 11]
    a.save_state(
        tmp_path / "evidence-600001.SH.json",
        {"freeze": case[1]["freeze"], "packet": case[3]},
    )
    fake_model(monkeypatch, case)
    result = run_case(tmp_path, case)
    assert result["status"] == "scoped_complete"
    assert result["evidence_review"] == "passed"
    summary = d.report_summary_status(d.structured_report(result))
    assert "有 2 页原文未纳入分析，结论范围受限" in summary
