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
    for section in ("summary", "business", "financial", "valuation", "counterevidence"):
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
            [error] = payload["errors"]
            assert (error["id"], error["reason"]) == ("F1", "citation_mismatch")
            assert error["detail"]["quote_diverges_at"] == "这不是逐字原文。"
            assert error["detail"]["source_continues"].startswith("\n经营活动产生的现金流量")
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


@pytest.mark.parametrize("section", ["summary", "business", "financial", "counterevidence"])
def test_unsupported_required_section_hides_draft(tmp_path, monkeypatch, case, section):
    case[4]["analysis"][section]["facts"] = ["F1"]
    calls, _ = fake_model(monkeypatch, case)
    result = run_case(tmp_path, case)
    assert_hidden(result)
    assert "rejected_draft" not in result
    assert result["diagnostic"] == {
        "stage": "citation_validation",
        "category": "section_unsupported",
        "message": "结论、业务、财务或主要风险缺少通过校验的原文事实或数字出处；未发布草稿。",
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
    final, dropped, unformed = d.prune_report(case[4], d.citation_errors(case[4], case[3]), case[3])
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
    public = d.structured_report(result)
    assert d.report_summary_status(public) == "研究完成"
    assert "有 2 页未送入分析" in d.scope_text(public)


def test_citation_details_locate_skipped_table_line(case):
    # Production 002236.SZ: a multi-row table quote silently dropped a wrapped "（元）" line.
    table = (
        "营业收入（元）           16,703,694,265.38    15,181,329,651.13                10.03%\n"
        "归属于上市公司股东的净利润\n"
        "                   2,382,974,277.66     2,475,566,750.61                 -3.74%\n"
        "（元）\n"
        "归属于上市公司股东的扣除非\n"
        "                   1,944,242,113.86     1,795,933,411.70                 8.26%\n"
    )
    case[3]["documents"][0]["pages"][0]["text"] += "\n" + table
    report = fixed_report(case)
    report["facts"][0]["quote"] = table.replace(
        "（元）\n归属于上市公司股东的扣除非", "归属于上市公司股东的扣除非"
    )
    report["facts"][1]["claim"] = "2024年" + report["facts"][1]["claim"]
    report["facts"][2]["page"] = 9
    errors = d.citation_errors(report, case[3])
    assert errors == {
        "F1": "citation_mismatch",
        "F2": "numeric_mismatch",
        "F3": "citation_mismatch",
    }
    details = {e["id"]: e for e in d.citation_details(report, case[3], errors)}
    skipped = details["F1"]["detail"]
    assert skipped["quote_diverges_at"].startswith("归属于上市公司股东的扣除非")
    assert skipped["source_continues"].startswith("\n（元）\n归属于上市公司股东的扣除非")
    assert details["F2"]["detail"] == {"numbers_not_in_quote": ["2024"]}
    assert details["F3"]["detail"] == {"matched_chars": 0, "note": "引用的页不存在"}
    # Diagnostics never relax the reason codes used for pruning.
    assert d.citation_errors(report, case[3]) == errors


def test_regressed_repair_keeps_better_first_round(tmp_path, monkeypatch, case):
    repaired = fixed_report(case)
    for fact in repaired["facts"][1:4]:
        fact["quote"] += "修复改坏。"
    calls, _ = fake_model(monkeypatch, case, repaired)
    result = run_case(tmp_path, case)
    assert_published(result, 4)
    assert result["quality"]["repair"] == "repaired"
    assert result["quality"]["dropped"] == [{"id": "F1", "reason": "citation_mismatch"}]
    assert [f["id"] for f in result["report"]["facts"]] == ["F2", "F3", "F4", "F5"]
    assert calls[-1].endswith("-verify")


def number_case():
    packet, report = __import__("tests.test_automation", fromlist=["x"]).packet_and_report()
    quote = "营业收入（元） 16,703,694,265.38 15,181,329,651.13 10.03% 经营活动产生的现金流量净额 -1,107,268,584.24"
    packet["documents"][0]["pages"].append({"page": 2, "text": quote})
    packet["documents"][0].update(period="2026-06-30", announced="2026-08-15")
    packet["structured"]["market_snapshot"] = {
        "date": "2026-10-16",
        "close": 33.72,
        "pe_ttm": 25.41,
    }
    report["facts"].append(
        {
            "id": "F5",
            "document": "123",
            "page": 2,
            "quote": quote,
            "claim": "营业收入同比增长10.03%",
        }
    )
    return packet, report


def test_analysis_numbers_allow_unit_conversion_rounding_sign_and_snapshot():
    packet, report = number_case()
    report["analysis"]["financial"] = {
        "text": "截至6月30日，2026年上半年营收167.04亿元（约167亿），同比增长10.03%（约10%）；经营现金流净流出11.07亿元。分两个板块，见F5。",
        "facts": ["F5", "F1"],
    }
    report["analysis"]["valuation"] = {"text": "收盘33.72元，市盈率约25.4倍。", "facts": ["MKT"]}
    assert d.analysis_number_errors(report, packet) == {}
    d.validate_report(report, packet)


def test_unsourced_numbers_withdraw_optional_and_fail_required_sections():
    packet, report = number_case()
    report["analysis"]["valuation"] = {"text": "市盈率约40倍，偏贵。", "facts": ["MKT"]}
    report["analysis"]["financial"] = {"text": "营收增长15%，2019年以来最快。", "facts": ["F5"]}
    assert d.analysis_number_errors(report, packet) == {
        "valuation": ["40"],
        "financial": ["2019年", "15%"],
    }
    pruned, dropped, missing = d.prune_report(report, {}, packet)
    assert pruned["analysis"]["valuation"] == {"text": d.UNFORMED, "facts": []}
    assert missing == {"valuation": "numbers_unsupported"}
    assert not d.supported(pruned["analysis"]["financial"])
    assert d.support_rank(pruned)[0] is False
    with pytest.raises(ScreenError, match="Analysis number"):
        d.validate_report(report, packet)
    # Each section is checked only against its own cited facts.
    report["analysis"]["financial"] = {"text": "营收增长10.03%。", "facts": ["F1"]}
    assert d.analysis_number_errors(report, packet)["financial"] == ["10.03%"]


def test_market_reference_needs_snapshot_and_never_supports_required_section():
    packet, report = number_case()
    report["analysis"]["business"]["facts"] = ["MKT"]
    with pytest.raises(ScreenError, match="Unbound analysis"):
        d.check_structure(report, packet)
    report["analysis"]["business"]["facts"] = ["F1", "MKT"]
    d.check_structure(report, packet)
    del packet["structured"]["market_snapshot"]
    with pytest.raises(ScreenError, match="Unbound analysis"):
        d.check_structure(report, packet)


def test_evidence_packet_carries_market_snapshot_in_yi():
    row = {
        "code": "603606.SH",
        "name": "合成",
        "industry": "电气设备",
        "pb": 3.1,
        "valuation_date": "2026-10-16",
        "annual_roes": [],
        "roe_mean": 12.0,
        "research_report": {},
        "close": 33.72,
        "pe_ttm": 25.41,
        "dv_ttm": 1.2,
        "total_mv": 2318960.5,
    }
    doc = {"id": "1", "pages": [{"page": 1, "text": "x" * 20}]}
    snapshot = d.evidence_packet([doc], row)["structured"]["market_snapshot"]
    assert snapshot == {
        "date": "2026-10-16",
        "close": 33.72,
        "pe_ttm": 25.41,
        "pb": 3.1,
        "total_mv_yi": 231.9,
        "dv_ttm": 1.2,
    }


def test_number_guard_keeps_percent_and_amount_apart_and_accepts_cited_prior_years():
    packet, report = number_case()
    quote = "2025年毛利率15% 第1.2.3节 2025.12.31 营业收入（元） 3,750,000,000"
    packet["documents"][0]["pages"][0]["text"] += "\n" + quote
    report["facts"].append(
        {"id": "F6", "document": "123", "page": 1, "quote": quote, "claim": "毛利率15%"}
    )
    report["analysis"]["financial"] = {
        "text": "2025年毛利率15%，营收37.5亿元；见第1.2.3节，截至2025.12.31。",
        "facts": ["F6"],
    }
    assert d.analysis_number_errors(report, packet) == {}
    # A percentage cannot be restated as an amount, nor a scaled amount as a percentage.
    report["analysis"]["financial"]["text"] = "净利润15亿元，订单下滑37.5%。"
    assert d.analysis_number_errors(report, packet) == {"financial": ["15", "37.5%"]}


def test_percent_column_and_page_level_number_sources():
    # Production 603606.SH: a one-row quote under a "(%)" header, and section figures that sit
    # on the cited page next to a narrower quote.
    page = "本报告期比上年同期增减(%)\n营业收入 5,799,552,532.02 4,432,278,764.22 30.85\n归属于上市公司股东的净利润 564,785,958.32 472,962,984.86 19.41"
    fact = {
        "id": "F1",
        "document": "1",
        "page": 1,
        "quote": "营业收入 5,799,552,532.02 4,432,278,764.22 30.85",
        "claim": "营业收入同比增减为30.85%",
    }
    assert d.unquoted_numbers(fact, None, page) == []
    assert d.unquoted_numbers(fact, None, "") == ["30.85%"]
    assert d.unquoted_numbers(fact | {"claim": "营业收入同比增减为-30.85%"}, None, page) == [
        "-30.85%"
    ]
    packet = {
        "structured": {"code": "603606.SH"},
        "documents": [
            {
                "id": "1",
                "period": "2026-06-30",
                "announced": "2026-08-05",
                "pages": [{"page": 1, "text": page}],
            }
        ],
    }
    report = {
        "facts": [fact],
        "analysis": {
            "financial": {
                "text": "收入57.9955亿元，增长30.85%；归母净利润5.6479亿元，增长19.41%。",
                "facts": ["F1"],
            }
        },
    }
    assert d.analysis_number_errors(report, packet) == {}
    report["analysis"]["financial"]["text"] = "归母净利润6.1亿元。"
    assert d.analysis_number_errors(report, packet) == {"financial": ["6.1"]}


def test_number_guard_rejects_unit_crossing_small_amounts_and_invented_dates():
    # Codex review 2026-10-19: each case below used to pass validate_report.
    page = "营业收入（元） 4,275,000,000 同比增长10.03% 截至 2026 年 8 月 3 日 在手订单 179.34 亿元"
    packet = {
        "structured": {"code": "x", "market_snapshot": {"close": 33.72, "date": "2026-10-16"}},
        "documents": [
            {
                "id": "1",
                "period": "2026-06-30",
                "announced": "2026-08-15",
                "pages": [{"page": 1, "text": page}],
            }
        ],
    }
    fact = {"id": "F1", "document": "1", "page": 1, "quote": page, "claim": "c"}

    def errors(text, facts=("F1",)):
        report = {"facts": [fact], "analysis": {"s": {"text": text, "facts": list(facts)}}}
        return d.analysis_number_errors(report, packet).get("s", [])

    assert errors("营收33.72亿元。") == ["33.72"]  # snapshot counts only with MKT
    assert errors("收盘33.72元，快照截至2026年10月16日。", ("MKT",)) == []
    assert errors("净利润12亿元。") == ["12"]
    assert errors("分3个板块，6月30日止。") == []
    assert errors("2026年12月31日有新订单。") == ["2026年12月31日"]
    assert errors("截至2026年8月3日在手订单179.34亿元，营收42.75亿元，增长10.03%。") == []
    # A stray "%" elsewhere in the quote no longer licenses "%" on another number.
    claim = {"quote": "营业收入（元）42.75，同比增长10.03%", "claim": "收入同比增长42.75%"}
    assert d.unquoted_numbers(claim, None, "") == ["42.75%"]
