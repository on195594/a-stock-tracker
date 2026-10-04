"""Research evidence and direct-company workflows use synthetic data only."""

import json
from pathlib import Path

import pytest

from a_stock_tracker import services, watch, worker
from a_stock_tracker.auth import AuthError, create_demo_actor
from a_stock_tracker.evidence import (
    REPORT_FIELDS,
    report_usable,
    research_prompt,
    select_latest_report,
)
from a_stock_tracker.workspace import (
    WorkspaceError,
    connect_workspace,
    import_snapshot,
    initialize,
    utc_now,
)


def report_row(**changes):
    return {
        "end_date": "20260630",
        "ann_date": "20260820",
        "update_flag": "0",
        "source": "tushare.fina_indicator",
        "acquired_at": "2026-09-20T16:00:00+08:00",
        **{field: 2.0 for field in REPORT_FIELDS},
        **changes,
    }


def test_latest_interim_report_selection_and_missing_values():
    annual = report_row(end_date="20251231", ann_date="20260420")
    latest = select_latest_report([annual, report_row()], "2026-09-20", "2026-09-21T10:00:00+08:00")
    assert latest["period"] == "2026-06-30" and report_usable(latest)
    incomplete = select_latest_report(
        [annual, report_row(ocfps=None)], "2026-09-20", "2026-09-21T10:00:00+08:00"
    )
    assert incomplete["status"] == "partial" and incomplete["metrics"]["ocfps"] is None
    assert incomplete["period"] == "2026-06-30" and not report_usable(incomplete)
    assert "补齐" in research_prompt({"research_report": incomplete})["action"]


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf")])
def test_invalid_metric_never_becomes_a_fact(bad):
    selected = select_latest_report([report_row(debt_to_assets=bad)], "2026-09-20", "2026-09-21")
    assert selected["status"] == "partial" and not report_usable(selected)


def test_report_versions_dates_and_evidence_bound_prompt():
    original = report_row()
    conflicting = report_row(ocfps=-2)
    conflict = select_latest_report([original, conflicting], "2026-09-20", "2026-09-21")
    assert conflict["status"] == "conflict"
    revised = select_latest_report(
        [original, {**conflicting, "update_flag": "1"}], "2026-09-20", "2026-09-21"
    )
    assert revised["metrics"]["ocfps"] == -2 and report_usable(revised)
    prompt = research_prompt({"research_report": revised})
    assert "现金流" in prompt["action"] and "不能仅据此认定经营恶化" in prompt["why"]
    assert (
        select_latest_report([report_row(ann_date="20260601")], "2026-09-20", "2026-09-21")[
            "status"
        ]
        == "conflict"
    )
    assert (
        select_latest_report([report_row(ann_date="20260922")], "2026-09-20", "2026-09-21")[
            "status"
        ]
        == "missing"
    )


@pytest.mark.parametrize("profit", [-2, 0, 2])
@pytest.mark.parametrize("cash", [-2, 0, 2])
def test_profit_and_cash_prompts_keep_both_facts_and_single_risk_behavior(profit, cash):
    report = select_latest_report(
        [report_row(dt_netprofit_yoy=profit, ocfps=cash)], "2026-09-20", "2026-09-21"
    )
    prompt = research_prompt({"research_report": report})
    assert prompt["action"] == (
        "复核盈利下滑是否影响原判断"
        if profit < 0
        else "先核查经营现金流"
        if cash < 0
        else "先核查业务可比性与盈利质量"
    )
    if profit < 0 or cash < 0:
        why, next_step = [], []
        if profit < 0:
            why.append("本次报告扣非归母净利润同比 -2.00%；尚未核实原因。")
            next_step.append("查管理层经营讨论和非经常性损益说明，核对需求、毛利与费用变化。")
        if cash < 0:
            why.append("本次报告每股经营现金流 -2.00 元/股；不能仅据此认定经营恶化。")
            next_step.append("查现金流量表及应收、存货附注，核对季节性和营运资金占用。")
        assert prompt["why"] == " ".join(why)
        assert prompt["next"] == " ".join(next_step)


@pytest.mark.parametrize("status", ["ok", "failed", "missing", "conflict", "partial"])
@pytest.mark.parametrize(
    "risk", [{}, {"risk_status": "known_warning"}, {"list_status": "D"}, {"list_status": "P"}]
)
def test_risk_and_evidence_gaps_take_precedence_over_both_negative_metrics(status, risk):
    report = select_latest_report(
        [report_row(dt_netprofit_yoy=-2, ocfps=-2)], "2026-09-20", "2026-09-21"
    )
    if status == "partial":
        report.update(status=status, missing=["debt_to_assets"])
        report["metrics"]["debt_to_assets"] = None
    elif status != "ok":
        report = {"status": status}
    row = {"research_report": report, **risk}
    prompt = research_prompt(row)
    if risk or status != "ok":
        assert prompt["action"] == ("先核查公司风险状态" if risk else "先补齐最新财报证据")
        assert "每股经营现金流" not in prompt["why"]
        assert "盈利下滑" not in prompt["action"]
    else:
        assert prompt["action"] == "复核盈利下滑是否影响原判断"
        assert "每股经营现金流" in prompt["why"]


def test_direct_company_is_frozen_idempotent_and_does_not_auto_follow(tmp_path):
    initialize(tmp_path, "demo", journal_mode="DELETE")
    actor = create_demo_actor()
    job = services.request_company_update(actor, "600002", "direct-first", tmp_path, "demo")
    assert job["codes"] == ["600002.SH"] and job["company_code"] == "600002.SH"
    assert (
        services.request_company_update(actor, "600002.SH", "direct-alias", tmp_path, "demo")[
            "job_id"
        ]
        == job["job_id"]
    )
    with pytest.raises(services.ServiceError, match="不同更新意图"):
        services.request_company_update(actor, "600003", "direct-first", tmp_path, "demo")
    with pytest.raises(services.ServiceError, match="不同更新意图"):
        services.request_watch_update(actor, "direct-first", tmp_path, "demo")
    worker.run_worker(tmp_path, "demo", once=True)
    result = services.get_update_job(actor, job["job_id"], tmp_path, "demo")
    assert result["status"] == "succeeded"
    ctx = services.get_company_context(actor, "600002.SH", tmp_path, "demo")
    assert ctx["research_report"]["period"] == "2026-06-30" and not ctx["is_watched"]
    saved = services.save_watch(
        actor,
        "600002.SH",
        result["result_run_id"],
        {"reason": "核实业务与现金流", "next_check": "下一份季报核查盈利"},
        0,
        tmp_path,
        "demo",
    )
    assert saved["ack_run_id"] is None
    actor.revoke()
    with pytest.raises(AuthError, match="expired|revoked|授权"):
        services.request_company_update(actor, "600002", "revoked", tmp_path, "demo")


@pytest.mark.parametrize(
    "code", ["688001", "300001", "600002.SZ", "000001.SH", "garbage", "600001;drop"]
)
def test_direct_company_rejects_unsupported_codes_without_job(tmp_path, code):
    initialize(tmp_path, "demo", journal_mode="DELETE")
    with pytest.raises(services.ServiceError):
        services.request_company_update(create_demo_actor(), code, "invalid", tmp_path, "demo")
    with connect_workspace(tmp_path, "demo") as conn:
        assert conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0] == 0


def test_forged_direct_intent_cannot_expand_scope(tmp_path):
    initialize(tmp_path, "demo", journal_mode="DELETE")
    services.request_company_update(create_demo_actor(), "600002", "direct", tmp_path, "demo")
    job = worker._claim(tmp_path, "demo")
    payload = json.loads(job["payload_json"])
    payload["codes"].append("600003.SH")
    job["payload_json"] = json.dumps(payload)
    with pytest.raises(WorkspaceError, match="identity"):
        worker._payload(job)


def test_rechecking_same_report_is_not_a_financial_change(tmp_path):
    initialize(tmp_path, "demo", journal_mode="DELETE")
    actor = create_demo_actor()
    first = services.request_company_update(actor, "600002", "first", tmp_path, "demo")
    worker.run_worker(tmp_path, "demo", once=True)
    run = services.get_update_job(actor, first["job_id"], tmp_path, "demo")["result_run_id"]
    item = services.save_watch(actor, "600002.SH", run, {}, 0, tmp_path, "demo")
    services.mark_seen(actor, "600002.SH", run, item["revision"], tmp_path, "demo")
    services.request_company_update(actor, "600002", "second", tmp_path, "demo")
    worker.run_worker(tmp_path, "demo", once=True)
    assert services.get_home(actor, tmp_path, "demo")["important_review_count"] == 0
    ctx = services.get_company_context(actor, "600002.SH", tmp_path, "demo")
    assert ctx["comparison"]["can_ack"]
    overview = services.get_home(actor, tmp_path, "demo")["watch_items"][0]
    assert overview["usable_run_id"] == ctx["displayed_run_id"]
    assert overview["latest_report_period"] == ctx["research_report"]["period"]
    for field in ("dt_netprofit_yoy", "ocfps"):
        assert overview[field] == ctx["research_report"]["metrics"][field]
    assert not any(f["changed"] for f in ctx["comparison"]["items"])
    assert ctx["research_report"]["acquired_at"] <= utc_now()


def test_failed_update_does_not_present_previous_report_as_current(tmp_path, monkeypatch):
    initialize(tmp_path, "demo", journal_mode="DELETE")
    actor = create_demo_actor()
    job = services.request_company_update(actor, "600002", "first", tmp_path, "demo")
    worker.run_worker(tmp_path, "demo", once=True)
    run = services.get_update_job(actor, job["job_id"], tmp_path, "demo")["result_run_id"]
    services.save_watch(actor, "600002.SH", run, {}, 0, tmp_path, "demo")

    def fail(*args, **kwargs):
        raise worker.ScreenError("synthetic failure")

    monkeypatch.setattr(worker, "build_watch_snapshot", fail)
    services.request_company_update(actor, "600002", "failed", tmp_path, "demo")
    worker.run_worker(tmp_path, "demo", once=True)
    ctx = services.get_company_context(actor, "600002.SH", tmp_path, "demo")
    assert ctx["displayed_run_id"] == run and ctx["has_usable_facts"]
    assert ctx["has_latest_attempt_gap"] and not ctx["comparison"]["can_ack"]
    assert ctx["research_report"]["status"] == "failed"
    assert "metrics" not in ctx["research_report"]
    assert "补齐" in ctx["research_prompt"]["action"]
    home = services.get_home(actor, tmp_path, "demo")
    assert "补齐" in home["watch_items"][0]["research_prompt"]["action"]
    overview = home["watch_items"][0]
    old_report = ctx["usable_fact"]["research_report"]
    assert overview["change_tier"] == "anomaly"
    assert overview["usable_run_id"] == run
    assert overview["latest_report_period"] == old_report["period"]
    assert overview["dt_netprofit_yoy"] == old_report["metrics"]["dt_netprofit_yoy"]
    assert overview["ocfps"] == old_report["metrics"]["ocfps"]


@pytest.mark.parametrize(
    "changes",
    [{"source": []}, {"selection_basis": []}, {"metrics": [1]}, {"ann_date": "2026-09-22"}],
)
def test_malformed_report_is_not_usable_or_presented_as_evidence(changes):
    report = select_latest_report([report_row()], "2026-09-20", "2026-09-21")
    report.update(changes)
    assert not report_usable(report)
    assert "补齐" in research_prompt({"research_report": report})["action"]


def test_malformed_annual_evidence_does_not_crash_prompt():
    assert research_prompt({"annual_roes": [None]})


def test_imported_malformed_report_keeps_pages_readable_and_blocks_ack(tmp_path):
    initialize(tmp_path, "demo", journal_mode="DELETE")
    snap = json.loads((Path(__file__).parent / "fixtures/peer_complete_v1.json").read_text())
    row = next(r for r in snap["rows"] if r["code"] == "600002.SH")
    row["research_report"] = {"status": "partial", "metrics": [1]}
    snap["results"]["rows"] = snap["rows"]
    path = tmp_path / "malformed.json"
    path.write_text(json.dumps(snap))
    run = import_snapshot(tmp_path, path, "demo")
    actor = create_demo_actor()
    item = services.save_watch(actor, "600002.SH", run, {}, 0, tmp_path, "demo")
    ctx = services.get_company_context(actor, "600002.SH", tmp_path, "demo")
    assert ctx["research_report"]["status"] == "conflict"
    assert "metrics" not in ctx["research_report"]
    assert not ctx["comparison"]["can_ack"]
    assert services.get_home(actor, tmp_path, "demo")["important_review_count"] == 1
    overview = services.get_home(actor, tmp_path, "demo")["watch_items"][0]
    for field in (
        "usable_run_id",
        "pb",
        "roe_mean",
        "latest_report_period",
        "dt_netprofit_yoy",
        "ocfps",
    ):
        assert overview[field] is None
    with pytest.raises(services.ServiceError, match="usable facts"):
        services.mark_seen(actor, "600002.SH", run, item["revision"], tmp_path, "demo")


def test_partial_report_structure_is_checked_before_publishing(tmp_path):
    initialize(tmp_path, "demo", journal_mode="DELETE")
    services.request_company_update(create_demo_actor(), "600002", "partial", tmp_path, "demo")
    job = worker._claim(tmp_path, "demo")
    payload = worker._payload(job)
    snap = watch.build_watch_snapshot(
        payload["codes"], payload["target_date"], "demo", lambda: False
    )
    snap["workspace_meta"] = {
        "job_id": job["job_id"],
        "intent_hash": payload["intent_hash"],
        "target_date": payload["target_date"],
        "anchor": None,
        "expected_codes": payload["codes"],
    }
    report = snap["rows"][0]["research_report"]
    report.update(status="partial", missing=["ocfps"])
    report["metrics"]["ocfps"] = None
    snap["rows"][0]["facts_usable"] = False
    assert watch.validate_watch_snapshot(snap, payload, job, "demo") == "partial"
    report["metrics"] = [1]
    with pytest.raises(WorkspaceError, match="invalid research report"):
        watch.validate_watch_snapshot(snap, payload, job, "demo")


def test_annual_prompt_uses_report_order_not_array_order():
    annual = [{"year": year, "roe": roe} for year, roe in [(2023, 10), (2024, 8), (2025, 6)]]
    prompt = research_prompt({"annual_roes": annual})
    assert "从 8.00% 降至 6.00%" in prompt["why"]
    assert research_prompt({"annual_roes": list(reversed(annual))}) == prompt


@pytest.mark.parametrize("acknowledge_first", [True, False])
def test_report_regression_blocks_review_even_without_a_baseline(
    tmp_path, monkeypatch, acknowledge_first
):
    initialize(tmp_path, "demo", journal_mode="DELETE")
    actor = create_demo_actor()
    job = services.request_company_update(actor, "600002", "first", tmp_path, "demo")
    worker.run_worker(tmp_path, "demo", once=True)
    first = services.get_update_job(actor, job["job_id"], tmp_path, "demo")["result_run_id"]
    item = services.save_watch(actor, "600002.SH", first, {}, 0, tmp_path, "demo")
    if acknowledge_first:
        item = services.mark_seen(actor, "600002.SH", first, item["revision"], tmp_path, "demo")
    build = worker.build_watch_snapshot

    def regressed(*args, **kwargs):
        snapshot = build(*args, **kwargs)
        snapshot["rows"][0]["research_report"].update(period="2026-03-31", ann_date="2026-04-20")
        return snapshot

    monkeypatch.setattr(worker, "build_watch_snapshot", regressed)
    second = services.request_company_update(actor, "600002", "second", tmp_path, "demo")
    worker.run_worker(tmp_path, "demo", once=True)
    second_run = services.get_update_job(actor, second["job_id"], tmp_path, "demo")["result_run_id"]
    ctx = services.get_company_context(actor, "600002.SH", tmp_path, "demo")
    assert ctx["has_latest_attempt_gap"] and not ctx["comparison"]["can_ack"]
    assert ctx["displayed_run_id"] == first
    assert services.get_home(actor, tmp_path, "demo")["important_review_count"] == 1
    for run in (first, second_run):
        with pytest.raises(services.ServiceError, match="regressed|倒退|data gap"):
            services.mark_seen(actor, "600002.SH", run, item["revision"], tmp_path, "demo")
