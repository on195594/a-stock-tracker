"""Synthetic model responses exercise billing paths and dependency failures without a provider."""

import copy
import json

import pytest

from a_stock_tracker import evidence_blocks as eb
from a_stock_tracker import evidence_pipeline as p
from a_stock_tracker.automation import Budget, BudgetStop, digest, encoded
from a_stock_tracker.disclosures import quality_text
from tests.test_evidence_blocks import TEXT, document


def evidence_case():
    docs = [document(TEXT)]
    structured = {"code": "600001.SH", "market_snapshot": {"date": "2026-10-19"}}
    packet = {
        **eb.select_blocks(docs, structured),
        "documents": docs,
        "structured": structured,
        "gaps": [],
    }
    blocks = packet["blocks"]
    facts = [
        {
            "id": "F1",
            "block_id": next(b["id"] for b in blocks if "business" in b["groups"]),
            "claim": "公司主要业务为生产合成设备。",
        },
        {
            "id": "F2",
            "block_id": next(b["id"] for b in blocks if b["relation"]["kind"] == "same_page_table"),
            "claim": "营业收入120.00元。",
        },
        {
            "id": "F3",
            "block_id": next(b["id"] for b in blocks if "risk" in b["groups"]),
            "claim": "主要风险为应收账款回收不确定性。",
        },
    ]
    analysis = {s: [] for s in p.SECTIONS}
    for section, fid, text in [
        ("summary", "F3", "需要谨慎，应收账款回收存在不确定性。"),
        ("business", "F1", facts[0]["claim"]),
        ("financial", "F2", facts[1]["claim"]),
        ("counterevidence", "F3", facts[2]["claim"]),
    ]:
        analysis[section] = [
            {"id": f"S_{section}_1", "kind": "assertion", "text": text, "refs": [fid]}
        ]
    report = {
        "version": p.VERSION,
        "code": structured["code"],
        "facts": facts,
        "analysis": analysis,
        "gaps": [],
    }
    return packet, report


@pytest.fixture
def case():
    return evidence_case()


def supported(report):
    return {
        "facts": [
            {"id": f["id"], "supported": True, "reason": "合成原文支持"} for f in report["facts"]
        ],
        "sentences": [
            {"id": sid, "supported": True, "reason": "合成原文支持"}
            for sid in p.sentence_map(report["analysis"])
        ],
        "coverage": {g: True for g in eb.REQUIRED_GROUPS},
        "issues": [],
    }


def replacement(sentence):
    return {"action": "replace", "sentence": sentence}


def test_report_and_review_bind_every_fact_and_sentence(case):
    packet, report = case
    p.validate_report(report, packet)
    review = {**supported(report), "report_hash": digest(report)}
    assert p.validate_review(review, report)
    changed = copy.deepcopy(report)
    changed["analysis"]["summary"][0]["text"] += "未经核查的结论"
    assert not p.validate_review(review, changed)
    review["sentences"].pop()
    assert not p.validate_review(review, report)


def test_every_selected_risk_fact_must_appear_in_counterevidence(case):
    packet, report = case
    extra = {**report["facts"][2], "id": "F4"}
    report["facts"].append(extra)
    report["analysis"]["summary"][0]["refs"].append("F4")
    error = next(
        e for e in p.composition_errors(report, packet) if e["reason"] == "risk_facts_uncited"
    )
    assert error["missing_fact_ids"] == ["F4"]


def test_fact_deletion_invalidates_all_cross_section_dependents(case):
    packet, report = case
    errors = [{"kind": "fact", "id": "F3", "reason": "unsupported"}]
    targets = p.repair_targets(report, errors, packet)
    patch = {"facts": {"F3": None}, "sentences": {}}
    with pytest.raises(p.PipelineError, match="dependency_not_repaired"):
        p.apply_patch(report, patch, targets)
    for sid in targets["sentences"]:
        old = p.sentence_map(report["analysis"])[sid]
        patch["sentences"][sid] = {
            "action": "reconfirm",
            "old_hash": digest(old),
            "refs": old["refs"],
        }
    with pytest.raises(p.PipelineError, match="dependency_not_repaired"):
        p.apply_patch(report, patch, targets)
    for sid in targets["sentences"]:
        old = p.sentence_map(report["analysis"])[sid]
        patch["sentences"][sid] = replacement({**old, "refs": ["F1"]})
    with pytest.raises(p.PipelineError, match="dependency_not_repaired"):
        p.apply_patch(report, patch, targets)


def test_sentence_only_repair_freezes_facts_and_other_sections(case):
    packet, report = case
    sid = "S_summary_1"
    targets = p.repair_targets(
        report, [{"kind": "sentence", "id": sid, "reason": "unsupported"}], packet
    )
    assert targets["facts"] == [] and targets["sentences"] == [sid]
    patch = {
        "facts": {},
        "sentences": {
            sid: replacement(
                {**report["analysis"]["summary"][0], "text": "需要谨慎，回款存在不确定性。"}
            )
        },
    }
    result = p.apply_patch(report, patch, targets)
    assert result["facts"] == report["facts"]
    assert result["analysis"]["business"] == report["analysis"]["business"]
    patch["facts"]["F1"] = report["facts"][0]
    with pytest.raises(p.PipelineError, match="patch_scope"):
        p.apply_patch(report, patch, targets)


def test_repair_budget_holds_the_frozen_compose_context_and_whole_report():
    # A section repair carries every frozen fact block (bounded like compose) plus the report;
    # prod reports with six facts needed ~19 KB before any alternative under the old 18 KB.
    floor = p.CONTEXT_LIMITS["compose"] + p.REPORT_LIMIT + len(p.REPAIR.encode())
    assert p.CONTEXT_LIMITS["repair"] >= floor


def test_deleting_an_unused_new_sentence_slot_is_a_no_op(case):
    packet, report = case
    errors = [{"kind": "section", "id": "counterevidence", "reason": "遗漏风险"}]
    targets = p.repair_targets(report, errors, packet)
    slot = targets["new_sentences"][-1]
    result = p.apply_patch(
        report, {"facts": {}, "sentences": {slot: {"action": "delete"}}}, targets
    )
    assert result == report
    with pytest.raises(p.PipelineError, match="patch_scope"):
        p.apply_patch(report, {"facts": {}, "sentences": {"S_x_9": {"action": "delete"}}}, targets)


@pytest.mark.parametrize(
    "failure,expected",
    [
        (None, ["select", "compose", "review"]),
        ("select", ["select", "repair", "compose", "review"]),
        ("compose", ["select", "compose", "repair", "review"]),
        ("review", ["select", "compose", "review", "repair", "recheck"]),
        # A mechanical slip no longer spends the repair a review finding needs.
        ("compose+review", ["select", "compose", "repair", "review", "repair2", "recheck"]),
        # Two mechanical slips still stop: each kind of repair is used once.
        ("select+compose", None),
    ],
)
def test_bounded_paths_and_same_request_replay(case, tmp_path, failure, expected):
    packet, original = case
    stages = []
    state = {"model_calls": 0, "model": "synthetic/model"}

    def call(directory, label, prompt, model, state, budget):
        stage = label.rsplit("-", 1)[-1]
        payload = json.loads(prompt[len(p.PROMPTS[stage]) :])
        response_path = directory / f"{label}.response.json"
        if response_path.exists():
            return json.loads(response_path.read_text())
        stages.append(stage)
        state["model_calls"] += 1
        (directory / f"{label}.request.json").write_bytes(encoded({"prompt": prompt}))
        if stage == "select":
            result = {k: copy.deepcopy(original[k]) for k in ("code", "facts", "gaps")}
            if failure in ("select", "select+compose"):
                result["facts"][1]["claim"] = "营业收入999.00元。"
        elif stage == "compose":
            result = {k: copy.deepcopy(original[k]) for k in ("code", "analysis")}
            if failure in ("compose", "compose+review", "select+compose"):
                result["analysis"]["summary"][0]["text"] = "需要谨慎，2099年存在风险。"
        elif stage in ("repair", "repair2"):
            result = {"facts": {}, "sentences": {}}
            if failure.startswith("select") and stage == "repair":
                result["facts"]["F2"] = original["facts"][1]
            else:
                result["sentences"]["S_summary_1"] = replacement(original["analysis"]["summary"][0])
        else:
            result = supported(payload["subject_report"])
            if stage == failure or (failure == "compose+review" and stage == "review"):
                next(s for s in result["sentences"] if s["id"] == "S_summary_1").update(
                    supported=False, reason="合成语义失败"
                )
        response_path.write_bytes(encoded(result))
        return result

    quality = {}
    if expected is None:
        with pytest.raises(p.PipelineError, match="repair_exhausted"):
            p.run(packet, tmp_path, state, Budget(100, 1800), call, quality)
        assert stages == ["select", "repair", "compose"]
        return
    report, review = p.run(packet, tmp_path, state, Budget(100, 1800), call, quality)
    assert stages == expected
    assert p.validate_review(review, report)
    assert quality["passed"] == quality["mechanical_passed"] == quality["semantic_passed"] == 3
    replay, _ = p.run(packet, tmp_path, state, Budget(100, 1800), call, {})
    assert replay == report and stages == expected


@pytest.mark.parametrize(
    "recheck,outcome",
    [
        ("untouched_section", ["counterevidence"]),
        ("touched_section", None),
        ("untouched_sentence", None),
    ],
)
def test_recheck_keeps_first_verdict_only_for_untouched_sections(case, tmp_path, recheck, outcome):
    packet, original = case
    state = {"model_calls": 0, "model": "synthetic/model"}

    def call(directory, label, prompt, model, state, budget):
        stage = label.rsplit("-", 1)[-1]
        payload = json.loads(prompt[len(p.PROMPTS[stage]) :])
        state["model_calls"] += 1
        (directory / f"{label}.request.json").write_bytes(encoded({"prompt": prompt}))
        if stage == "select":
            return {k: copy.deepcopy(original[k]) for k in ("code", "facts", "gaps")}
        if stage == "compose":
            return {k: copy.deepcopy(original[k]) for k in ("code", "analysis")}
        if stage == "repair":
            fixed = {**original["analysis"]["summary"][0], "text": "需要谨慎，回款存在不确定性。"}
            return {"facts": {}, "sentences": {"S_summary_1": replacement(fixed)}}
        result = supported(payload["subject_report"])
        if stage == "review":
            next(s for s in result["sentences"] if s["id"] == "S_summary_1").update(
                supported=False, reason="合成语义失败"
            )
        elif recheck == "untouched_sentence":
            sid = original["analysis"]["counterevidence"][0]["id"]
            next(s for s in result["sentences"] if s["id"] == sid).update(supported=False)
        else:
            section = "counterevidence" if recheck == "untouched_section" else "summary"
            result["coverage"]["risk"] = recheck != "untouched_section"
            result["issues"] = [{"kind": "section", "id": section, "reason": "合成新意见"}]
        return result

    quality = {}
    if outcome is None:
        with pytest.raises(p.PipelineError, match="semantic_failed"):
            p.run(packet, tmp_path, state, Budget(100, 1800), call, quality)
        return
    report, review = p.run(packet, tmp_path, state, Budget(100, 1800), call, quality)
    # Codex review: the published review must still bind at read time.
    assert p.validate_review(review, report)
    assert quality["carried_sections"] == outcome and quality["passed"] == 3
    assert "未改动的主要风险提出新意见，沿用首轮复核结论" in quality_text(quality)
    assert report["analysis"]["summary"][0]["text"].startswith("需要谨慎")


@pytest.mark.parametrize("calls,seconds", [(8, 1800), (0, 700)])
def test_reserve_whole_remaining_path_before_first_charge(case, tmp_path, calls, seconds):
    packet, _ = case

    def forbidden(*args):
        pytest.fail("No call may start without room for the mandatory path")

    with pytest.raises(BudgetStop):
        p.run(
            packet,
            tmp_path,
            {"model_calls": calls, "model": "synthetic/model"},
            Budget(100, seconds),
            forbidden,
            {},
        )


def test_output_bound_is_enforced_before_review(case):
    _, report = case
    report["analysis"]["summary"][0]["text"] = "需要谨慎" + "风险" * 800
    report["analysis"]["business"][0]["text"] = "业务" * 800
    with pytest.raises(p.PipelineError, match="context_budget_exceeded"):
        p.validate_composition_shape({k: report[k] for k in ("code", "analysis")}, report["code"])


def test_repair_shrinks_alternatives_by_whole_review_context(case):
    packet, report = case
    extra_doc = document(TEXT + "\n" + "无关附录占用完整页空间。" * 2000, identity="extra")
    extra = next(
        b for b in eb.document_blocks(extra_doc)[0] if b["relation"]["kind"] == "same_page_table"
    )
    packet["documents"].append(extra_doc)
    packet["blocks"].append(extra)
    errors = [{"kind": "fact", "id": "F2", "reason": "unsupported_literal"}]
    assert p.review_size_bound(report, packet, [extra]) > p.CONTEXT_LIMITS["review"]
    targets, payload = p.repair_input(report, errors, packet)
    assert extra["id"] not in targets["blocks"]
    assert report["facts"][1]["block_id"] in targets["blocks"]
    # Frozen business and risk evidence are still present in the local repair context.
    assert {f["block_id"] for f in report["facts"]} <= {b["id"] for b in payload["blocks"]}
    assert p.review_size_bound(report, packet, payload["blocks"]) <= p.CONTEXT_LIMITS["review"]


def test_sentence_repair_has_no_fact_alternatives(case):
    packet, report = case
    targets, payload = p.repair_input(
        report, [{"kind": "sentence", "id": "S_summary_1", "reason": "unsupported"}], packet
    )
    assert targets["facts"] == targets["blocks"] == []
    assert {b["id"] for b in payload["blocks"]} == {f["block_id"] for f in report["facts"]}


def test_independent_risk_context_cannot_be_pruned_to_fit_repair(case):
    packet, report = case
    extra_doc = document(
        "风险提示：需要核实潜在诉讼及对外担保。\n\n" + "未知上下文。" * 4000, identity="risk-extra"
    )
    extra = next(b for b in eb.document_blocks(extra_doc)[0] if "risk" in b["groups"])
    packet["documents"].append(extra_doc)
    packet["blocks"].append(extra)
    with pytest.raises(p.PipelineError, match="context_budget_exceeded"):
        p.repair_input(
            report, [{"kind": "fact", "id": "F2", "reason": "unsupported_literal"}], packet
        )


def test_semantic_coverage_error_opens_risk_fact_and_full_dependency_closure(case):
    packet, report = case
    targets = p.repair_targets(
        report, [{"kind": "section", "id": "counterevidence", "reason": "coverage_failed"}], packet
    )
    assert targets["facts"] == ["F3"]
    assert "S_summary_1" in targets["sentences"]
    assert "S_business_1" not in targets["sentences"]


def test_claim_cannot_assemble_date_from_unrelated_source_numbers(case):
    packet, report = case
    doc = document("公司主要业务为合成设备制造，2026年有12个项目，另有31个客户。", identity="date")
    block = eb.document_blocks(doc)[0][0]
    packet["documents"].append(doc)
    packet["blocks"].append(block)
    report["facts"][0].update(
        block_id=block["id"], claim="2026年12月31日公司主要业务为合成设备制造。"
    )
    errors = p.selection_errors({k: report[k] for k in ("code", "facts", "gaps")}, packet)
    assert any(
        e["kind"] == "fact" and e["id"] == "F1" and "2026年12月31日" in e["missing"] for e in errors
    )


def test_same_claim_in_same_block_does_not_count_as_another_fact(case):
    packet, report = case
    report["facts"].append({**report["facts"][0], "id": "F4"})
    errors = p.selection_errors({k: report[k] for k in ("code", "facts", "gaps")}, packet)
    assert any(e["id"] == "F4" and e["reason"] == "duplicate_fact" for e in errors)


def test_compose_sends_shared_evidence_once_without_dropping_facts(case, tmp_path):
    packet, report = case
    report["facts"].append(
        {"id": "F4", "block_id": report["facts"][1]["block_id"], "claim": "净利润20.00元。"}
    )
    report["analysis"]["financial"].append(
        {"id": "S_financial_2", "kind": "assertion", "text": "净利润20.00元。", "refs": ["F4"]}
    )
    stages = []

    def call(directory, label, prompt, model, state, budget):
        stage = label.rsplit("-", 1)[-1]
        stages.append(stage)
        payload = json.loads(prompt[len(p.PROMPTS[stage]) :])
        if stage == "select":
            return {k: report[k] for k in ("code", "facts", "gaps")}
        if stage == "compose":
            assert payload["facts"] == report["facts"]
            assert payload["risk_fact_ids"] == ["F3"]
            expected = {f["block_id"] for f in report["facts"]}
            assert len(payload["blocks"]) == len(expected) == 3
            assert {b["id"] for b in payload["blocks"]} == expected
            assert all(b == p.block_map(packet)[b["id"]] for b in payload["blocks"])
            return {k: report[k] for k in ("code", "analysis")}
        return supported(payload["subject_report"])

    result, review = p.run(
        packet, tmp_path, {"model_calls": 0, "model": "synthetic/model"}, Budget(0, 1800), call, {}
    )
    assert stages == ["select", "compose", "review"]
    assert p.validate_review(review, result)


def test_repair_rejects_plain_text_and_accepts_explicit_sentence_object(case):
    packet, report = case
    sid = "S_counterevidence_1"
    targets = p.repair_targets(
        report,
        [{"kind": "section", "id": "counterevidence", "reason": "risk_facts_uncited"}],
        packet,
    )
    sentence = report["analysis"]["counterevidence"][0]
    with pytest.raises(ValueError):
        p.apply_patch(
            report, {"facts": {}, "sentences": {sid: replacement(sentence["text"])}}, targets
        )
    patched = p.apply_patch(
        report, {"facts": {}, "sentences": {sid: replacement(sentence)}}, targets
    )
    assert patched == report
    assert not p.composition_errors(patched, packet)


def test_review_section_issue_opens_its_facts_even_when_coverage_says_true(case):
    packet, report = case
    report["analysis"]["counterevidence"].append(
        {
            "id": "S_counterevidence_2",
            "kind": "assertion",
            "text": "营业收入120.00元。",
            "refs": ["F2"],
        }
    )
    review = {**supported(report), "report_hash": digest(report)}
    review["issues"] = [{"kind": "section", "id": "counterevidence", "reason": "遗漏重要财务变化"}]
    errors = p.review_errors(review, report)
    targets = p.repair_targets(report, errors, packet)
    assert targets["facts"] == ["F2", "F3"]
    assert "S_financial_1" in targets["sentences"]
    assert "S_summary_1" in targets["sentences"]
    assert "S_business_1" not in targets["sentences"]
    assert not p.validate_review(review, report)


def test_unknown_block_repair_gets_valid_alternatives(case):
    packet, report = case
    original = copy.deepcopy(report["facts"][0])
    report["facts"][0]["block_id"] = "unknown"
    targets, payload = p.repair_input(
        report, [{"kind": "fact", "id": "F1", "reason": "unknown_block"}], packet
    )
    assert original["block_id"] in targets["blocks"]
    assert any(b["id"] == original["block_id"] for b in payload["blocks"])
    patch = {"facts": {"F1": original}, "sentences": {}}
    for sid in targets["sentences"]:
        sentence = p.sentence_map(report["analysis"])[sid]
        patch["sentences"][sid] = replacement(sentence)
    fixed = p.apply_patch(report, patch, targets)
    assert fixed["facts"][0] == original
