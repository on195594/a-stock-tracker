from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from a_stock_tracker import automation as a
from a_stock_tracker import disclosures as d
from a_stock_tracker.research import ScreenError


def basic(index=1, **overrides):
    return {
        "ts_code": f"{600000 + index}.SH",
        "name": "合成制造",
        "market": "主板",
        "exchange": "SSE",
        "list_status": "L",
        "industry": "制造",
        **overrides,
    }


def records(code):
    return [
        {
            "ts_code": code,
            "end_date": f"{y}1231",
            "ann_date": f"{y + 1}0430",
            "update_flag": "0",
            "roe_waa": 10 + y % 3,
            **{k: 1 for k in a.REPORT_FIELDS},
        }
        for y in (2023, 2024, 2025)
    ]


def fixture(count=60):
    rows = [a.initial_row(basic(i), {"pb": 1 + i / 100}, "2026-09-30") for i in range(1, count + 1)]
    frozen = {
        "rule": a.RULE,
        "rows": rows,
        "data_date": "2026-09-30",
        "cutoff": "2026-10-06",
        "sample": [],
        "mode": "market",
    }
    return frozen, {"freeze": a.digest(frozen), "scan": {}}


class Client:
    def __init__(self):
        self.calls = []

    def fina_indicator(self, ts_code, **kwargs):
        self.calls.append(ts_code)
        return pd.DataFrame(records(ts_code))


def test_full_scope_over_fifty_and_resume(tmp_path):
    frozen, state = fixture()
    client = Client()
    rows = a.scan(tmp_path, frozen, state, client, "not-a-secret", a.Budget(500, 300))
    report = a.coverage(rows, frozen)
    assert report["total"] == 60 and report["counts"] == {"qualified": 60}
    assert len(report["groups"][0]["ranking"]) == 60
    assert report["groups"][0]["top"] == [r["code"] for r in frozen["rows"][:3]]
    assert len(client.calls) == 60
    a.scan(tmp_path, frozen, state, client, "not-a-secret", a.Budget(500, 300))
    assert len(client.calls) == 60
    # Recover an orphan after result publication but before manifest/cursor commit.
    state["scan"].pop(frozen["rows"][0]["code"])
    a.scan(tmp_path, frozen, state, client, "not-a-secret", a.Budget(500, 300))
    assert len(client.calls) == 60
    path = tmp_path / f"scan-{frozen['rows'][0]['code']}.json"
    path.write_text("{}")
    with pytest.raises(ScreenError):
        a.scan(tmp_path, frozen, state, client, "not-a-secret", a.Budget(500, 300))


def test_bounded_scan_leaves_tail_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(a.time, "sleep", lambda *_: None)
    frozen, state = fixture(5)
    budget = a.Budget(3, 300)
    client = a.MarketClient(Client(), budget)
    rows = a.scan(tmp_path, frozen, state, client, "not-a-secret", budget)
    report = a.coverage(rows, frozen)
    assert report["status"] == "partial"
    assert report["counts"] == {"qualified": 2, "pending": 3}
    assert report["groups"] == []
    rows = a.scan(tmp_path, frozen, state, client, "not-a-secret", a.Budget(0, 300))
    assert sum(r["status"] == "qualified" for r in rows) == 2


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf")])
def test_invalid_numbers_are_gaps(value):
    assert a.initial_row(basic(), {"pb": value}, "2026-09-30")["status"] == "gap"


@pytest.mark.parametrize("missing", ["industry", "name"])
@pytest.mark.parametrize(
    "code,exchange,market",
    [
        ("300001.SZ", "SZSE", "创业板"),
        ("688001.SH", "SSE", "科创板"),
        ("800001.BJ", "BSE", "北交所"),
    ],
)
def test_proven_outside_scope_missing_fields_do_not_block_main_board_groups(
    tmp_path, missing, code, exchange, market
):
    metadata = basic(ts_code=code, exchange=exchange, market=market)
    assert a.initial_row(metadata, {"pb": 1}, "2026-09-30")["reason"] == "OUTSIDE_SCOPE"
    metadata[missing] = None
    excluded = a.initial_row(metadata, {"pb": 1}, "2026-09-30")
    assert excluded["status"] == "excluded" and excluded["reason"] == "OUTSIDE_SCOPE"
    frozen, state = fixture(1)
    frozen["rows"].append(excluded)
    state["freeze"] = a.digest(frozen)
    report = a.coverage(
        a.scan(tmp_path, frozen, state, Client(), "synthetic", a.Budget(10, 300)), frozen
    )
    assert report["counts"] == {"qualified": 1, "excluded": 1}
    assert report["groups"][0]["top"] == ["600001.SH"]


def test_scope_and_financial_fail_closed():
    assert a.initial_row(basic(industry=None), {"pb": 1}, "2026-09-30")["status"] == "gap"
    assert a.initial_row(basic(name=None), {"pb": 1}, "2026-09-30")["status"] == "gap"
    conflict = basic(ts_code="300001.SZ", market="创业板", exchange="SSE", industry=None)
    assert a.initial_row(conflict, {"pb": 1}, "2026-09-30")["reason"] == "IDENTITY_CONFLICT"
    assert (
        a.initial_row(basic(industry="银行"), {"pb": 1}, "2026-09-30")["reason"] == "OUTSIDE_SCOPE"
    )
    assert a.initial_row(basic(exchange="SZSE"), {"pb": 1}, "2026-09-30")["status"] == "gap"
    assert a.initial_row(basic(name="*ST合成"), {"pb": 1}, "2026-09-30")["status"] == "excluded"
    with pytest.raises(ScreenError):
        a.initial_row(basic(ts_code="600001.sh"), {"pb": 1}, "2026-09-30")
    frozen, _ = fixture(1)
    damaged = records("600001.SH")
    damaged[-1]["ann_date"] = "202681"
    assert a.evaluate(frozen["rows"][0], damaged, frozen)["status"] == "gap"


def test_group_years_and_round_robin():
    frozen, _ = fixture(6)
    rows = [a.evaluate(r, records(r["code"]), frozen) for r in frozen["rows"]]
    for row in rows[3:]:
        row["industry"] = "另一行业"
    result = a.coverage(rows, frozen)
    queue = a.candidates(result, frozen)
    assert len(queue) == 5 and len(set(queue)) == 5
    assert (
        next(r for r in rows if r["code"] == queue[0])["industry"]
        != next(r for r in rows if r["code"] == queue[1])["industry"]
    )
    for annual in rows[-1]["annual_roes"]:
        annual["period"] = str(int(annual["period"][:4]) - 1) + "-12-31"
    assert len(a.coverage(rows, frozen)["groups"]) == 3


def test_data_gap_does_not_masquerade_as_complete_industry():
    frozen, _ = fixture(2)
    rows = [a.evaluate(r, records(r["code"]), frozen) for r in frozen["rows"]]
    rows[-1].update(status="gap", reason="MISSING_THREE_ANNUAL_REPORTS")
    summary = a.coverage(rows, frozen)
    assert summary["status"] == "partial" and summary["groups"] == []
    assert summary["counts"] == {"qualified": 1, "gap": 1}


def test_root_is_explicit_private_and_separate(tmp_path):
    production = tmp_path / "production"
    production.mkdir()
    (production / "workspace.db").write_bytes(b"do not touch")
    with pytest.raises(ScreenError):
        with a.locked_root(production):
            pass
    assert (production / "workspace.db").read_bytes() == b"do not touch"
    link = tmp_path / "link"
    link.symlink_to(production, target_is_directory=True)
    with pytest.raises(ScreenError):
        with a.locked_root(link):
            pass
    root = tmp_path / "automation"
    with a.locked_root(root):
        assert a.read_result(root / "automation.json") == a.MARKER
        with pytest.raises(ScreenError):
            with a.locked_root(root):
                pass


def test_root_can_retry_after_marker_write_failure(tmp_path, monkeypatch):
    root = tmp_path / "automation"
    with monkeypatch.context() as patch:
        patch.setattr(a, "save", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk")))
        with pytest.raises(OSError, match="disk"):
            with a.locked_root(root):
                pass
    assert {p.name for p in root.iterdir()} == {".lock"}
    with a.locked_root(root):
        assert a.read_result(root / "automation.json") == a.MARKER


@pytest.mark.parametrize("entry", ["unrelated", "symlink", "directory"])
def test_root_does_not_adopt_unsafe_initialization_remnants(tmp_path, entry):
    root = tmp_path / "automation"
    root.mkdir(mode=0o700)
    lock = root / ".lock"
    if entry == "unrelated":
        lock.touch()
        (root / "workspace.db").write_bytes(b"private")
    elif entry == "symlink":
        lock.symlink_to(tmp_path / "missing")
    else:
        lock.mkdir()
    with pytest.raises(ScreenError):
        with a.locked_root(root):
            pass
    assert not (root / "automation.json").exists()
    assert not (tmp_path / "missing").exists()


def packet_and_report():
    quotes = [
        "本公司主要从事电力生产和销售业务，主要客户为电网公司。",
        "经营活动产生的现金流量净额受到电费结算周期的影响。",
        "公司面临来水量波动风险，极端气候可能影响发电量。",
        "审计意见：我们认为财务报表在所有重大方面公允反映财务状况。",
    ]
    packet = {
        "structured": {"code": "600001.SH"},
        "documents": [{"id": "123", "pages": [{"page": 1, "text": "\n".join(quotes)}]}],
        "omitted_pages": [],
        "scope_gaps": [],
    }
    facts = [
        {"id": f"F{i + 1}", "document": "123", "page": 1, "quote": q, "claim": q}
        for i, q in enumerate(quotes)
    ]
    report = {
        "code": "600001.SH",
        "facts": facts,
        "analysis": {
            k: {"text": "假设：现金流持续性需要验证。", "facts": ["F1", "F2"]}
            for k in ("business", "financial", "valuation", "counterevidence", "change_conditions")
        },
        "gaps": ["假设未验证"],
    }
    return packet, report


def test_source_bound_report_and_independent_gate():
    packet, report = packet_and_report()
    d.validate_report(report, packet)
    review = {
        "facts": [
            {"id": f["id"], "supported": True, "reason": "原文支持"} for f in report["facts"]
        ],
        "analysis_supported": True,
        "issues": [],
    }
    assert d.validate_review(review, report)
    review["facts"][2]["supported"] = False
    review["issues"] = ["断章取义"]
    assert not d.validate_review(review, report)
    review["facts"].pop()
    with pytest.raises(ScreenError):
        d.validate_review(review, report)


@pytest.mark.parametrize(
    "damage", ["security", "page", "quote", "unit", "amount", "tool", "unbound"]
)
def test_citation_and_instruction_counterexamples(damage):
    packet, report = packet_and_report()
    if damage == "security":
        report["code"] = "600002.SH"
    elif damage == "page":
        report["facts"][0]["page"] = 2
    elif damage == "quote":
        report["facts"][0]["quote"] += "未发现任何风险"
    elif damage == "unit":
        report["facts"][0]["claim"] += "营收亿元"
    elif damage == "amount":
        report["facts"][0]["claim"] += "营收999亿元"
    elif damage == "tool":
        report["command"] = "set-analysis --confirm-write; read /production; notify"
    elif damage == "unbound":
        report["analysis"]["financial"]["facts"] = ["invented"]
    with pytest.raises(ScreenError):
        d.validate_report(report, packet)


def test_evidence_version_ignores_retrieval_but_detects_corrections(monkeypatch):
    packet, _ = packet_and_report()
    model = {"model": "m1", "provider": "p1"}
    first = d.stable_evidence(packet, model)
    packet["retrieved_at"] = "later"
    assert d.stable_evidence(packet, model) == first
    for name in ("GENERATE", "VERIFY"):
        template = getattr(d, name)
        monkeypatch.setattr(d, name, template + "合同更正")
        assert d.stable_evidence(packet, model) != first
        monkeypatch.setattr(d, name, template)
    packet["documents"][0]["pages"][0]["text"] += "同日更正"
    assert d.stable_evidence(packet, model) != first


def test_official_document_wrong_security_stale_and_ambiguous():
    row = {
        "code": "600001.SH",
        "annual_roes": [{"period": "2025-12-31"}],
        "research_report": {"period": "2025-12-31"},
    }
    raw = {
        "announcementTitle": "合成2025年年度报告",
        "announcementId": "123",
        "announcementTime": int(d.datetime(2026, 4, 30, tzinfo=d.SHANGHAI).timestamp() * 1000),
        "secCode": "600001",
        "secName": "合成",
        "adjunctUrl": "finalpage/2026-04-30/123.PDF",
    }
    assert d.select_documents([raw], row, "2026-10-06")[0]["period"] == "2025-12-31"
    with pytest.raises(ScreenError):
        d.select_documents([{**raw, "secCode": "600002"}], row, "2026-10-06")
    with pytest.raises(ScreenError):
        d.select_documents([{**raw, "announcementTitle": "合成2024年年度报告"}], row, "2026-10-06")
    with pytest.raises(ScreenError):
        d.select_documents([raw, {**raw, "announcementId": "124"}], row, "2026-10-06")


@pytest.mark.parametrize("page_breaks", ["\f", "\f\f"])
def test_pdf_missing_page_or_identity_cannot_publish(tmp_path, monkeypatch, page_breaks):
    class Source:
        def request(self, *_):
            return b"%PDF-fake fixture"

    class Result:
        stdout = ""

    def command(args, **kwargs):
        result = Result()
        result.stdout = (
            "Pages: 2\n"
            if args[0] == "pdfinfo"
            else "600001 合成 2025 年报正文足够长的身份描述" + page_breaks
        )
        return result

    monkeypatch.setattr(d.subprocess, "run", command)
    with pytest.raises(ScreenError, match="Missing"):
        d.extract_document(
            Source(),
            {
                "url": "ignored",
                "code": "600001.SH",
                "official_name": "合成",
                "period": "2025-12-31",
            },
            tmp_path,
        )


def test_page_budget_is_explicit(monkeypatch):
    monkeypatch.setattr(d, "MAX_TEXT", 60)
    document = {"id": "123", "pages": [{"page": i + 1, "text": "财报正文" * 10} for i in range(3)]}
    row = {
        k: None
        for k in (
            "code",
            "name",
            "industry",
            "pb",
            "valuation_date",
            "annual_roes",
            "roe_mean",
            "research_report",
        )
    }
    packet = d.evidence_packet([document], row)
    assert len(packet["documents"][0]["pages"]) == 1 and len(packet["omitted_pages"]) == 2


def test_model_budget_and_no_implicit_runtime(tmp_path, monkeypatch):
    state = {"model_calls": 10}
    with pytest.raises(a.BudgetStop):
        d.model_call(
            tmp_path,
            "one",
            "public input",
            {"provider": "p", "model": "m"},
            state,
            a.Budget(1, 300),
        )
    monkeypatch.delenv("AUTOMATION_HERMES_RUNTIME", raising=False)
    state["model_calls"] = 0
    with pytest.raises(ScreenError, match="Explicit"):
        d.model_call(
            tmp_path,
            "one",
            "public input",
            {"provider": "p", "model": "m"},
            state,
            a.Budget(1, 300),
        )
    assert state["model_calls"] == 0


def research_case(directory):
    frozen, state = fixture(1)
    rows = [a.evaluate(r, records(r["code"]), frozen) for r in frozen["rows"]]
    summary = a.coverage(rows, frozen)
    state.update(
        research={},
        queue=a.candidates(summary, frozen),
        cursor=0,
        model_calls=0,
        model={"provider": "p", "model": "m"},
    )
    packet, report = packet_and_report()
    packet["structured"] = rows[0]
    packet["documents"][0].update(
        title="合成2025年年度报告",
        url="https://example.invalid/report.pdf",
        period="2025-12-31",
        announced="2026-04-30",
    )
    a.save_state(
        directory / "evidence-600001.SH.json", {"freeze": state["freeze"], "packet": packet}
    )
    review = {
        "facts": [
            {"id": f["id"], "supported": True, "reason": "原文支持"} for f in report["facts"]
        ],
        "analysis_supported": True,
        "issues": [],
    }
    return frozen, state, summary, packet, report, review


def test_api_budget_interruption_is_not_a_terminal_gap(tmp_path, monkeypatch):
    frozen, state = fixture(1)
    client = Client()
    monkeypatch.setattr(
        client, "fina_indicator", lambda **_: (_ for _ in ()).throw(a.BudgetStop("TIME_BUDGET"))
    )
    with pytest.raises(a.BudgetStop):
        a.scan(tmp_path, frozen, state, client, "not-a-secret", a.Budget(5, 300))
    assert state["scan"] == {} and not list(tmp_path.glob("scan-*.json"))


def test_cli_resume_fills_new_industries_and_finishes_bounded_queue(tmp_path, monkeypatch):
    import sys

    import tushare

    frozen, _ = fixture(6)
    frozen["created_at"] = "2026-10-06T10:00:00+08:00"
    for row in frozen["rows"][3:]:
        row["industry"] = "另一行业"
    root = tmp_path / "batch"
    budget = a.Budget(4, 300)
    client = a.MarketClient(Client(), budget)
    monkeypatch.setattr(a, "Budget", lambda *_: budget)
    monkeypatch.setattr(a, "MarketClient", lambda *_: client)
    monkeypatch.setattr(a.time, "sleep", lambda *_: None)
    monkeypatch.setattr(tushare, "pro_api", lambda *_, **__: None)
    monkeypatch.setattr(a, "freeze", lambda *_: frozen)
    monkeypatch.setattr(a, "now_iso", lambda: frozen["created_at"])
    monkeypatch.setattr(a.signal, "signal", lambda *_: None)
    monkeypatch.setattr(a.signal, "alarm", lambda *_: None)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-secret")

    def research(directory, frozen, summary, state, *args):
        state["cursor"] = len(state["queue"])
        a.save_state(directory / "state.json", state)

    monkeypatch.setattr(d, "research_batch", research)
    monkeypatch.setattr(sys, "argv", ["automation", "--root", str(root)])
    assert a.main() == 2
    directory = next(root.glob("run-*"))
    state = a.read_state(directory / "state.json")
    assert len(state["queue"]) == 3
    budget.used = 0
    budget.limit = 10
    monkeypatch.setattr(
        sys, "argv", ["automation", "--root", str(root), "--resume", directory.name]
    )
    assert a.main() == 0
    state = a.read_state(directory / "state.json")
    assert len(state["queue"]) == state["cursor"] == 5
    assert len({r["industry"] for r in frozen["rows"] if r["code"] in state["queue"]}) == 2


@pytest.mark.parametrize("outcome", ["passed", "review_failed", "source_failed", "scan_only"])
def test_cli_reports_actual_research_outcome(tmp_path, monkeypatch, capsys, outcome):
    import sys

    import tushare

    frozen, _, _, packet, report, review = research_case(tmp_path)
    frozen["created_at"] = "2026-10-06T10:00:00+08:00"
    calls = []

    def announcements(*_):
        calls.append("announcements")
        if outcome == "source_failed":
            raise ScreenError("synthetic source failure")
        return []

    def model(directory, label, *args):
        calls.append(label)
        return review if label.endswith("verify") else report

    if outcome == "review_failed":
        review["analysis_supported"] = False
        review["issues"] = ["合成复核失败"]
    monkeypatch.setattr(tushare, "pro_api", lambda *_, **__: Client())
    monkeypatch.setattr(a, "freeze", lambda *_: frozen)
    monkeypatch.setattr(a, "now_iso", lambda: frozen["created_at"])
    monkeypatch.setattr(a.time, "sleep", lambda *_: None)
    monkeypatch.setattr(a.signal, "signal", lambda *_: None)
    monkeypatch.setattr(a.signal, "alarm", lambda *_: None)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-secret")
    monkeypatch.setattr(d.OfficialSource, "announcements", announcements)
    monkeypatch.setattr(d, "select_documents", lambda *_: [])
    monkeypatch.setattr(d, "evidence_packet", lambda *_: packet)
    monkeypatch.setattr(d, "model_call", model)
    root = tmp_path / "batch"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "automation",
            "--root",
            str(root),
            "--max-research",
            "0" if outcome == "scan_only" else "5",
        ],
    )
    assert a.main() == (0 if outcome == "passed" else 2)
    output = json.loads(capsys.readouterr().out)
    directory = next(root.glob("run-*"))
    assert (directory / "summary.md").exists()
    expected_failed = int(outcome in {"review_failed", "source_failed"})
    assert output["failed_reports"] == expected_failed
    assert a.read_result(directory / "invocation.json")["failed_reports"] == expected_failed
    expected_counts = (
        {}
        if outcome == "scan_only"
        else {"incomplete" if expected_failed else "scoped_complete": 1}
    )
    assert output["report_counts"] == expected_counts
    if outcome == "scan_only":
        assert calls == [] and output["pending_candidates"] == 1
        assert "待研究 1 家" in (directory / "summary.md").read_text()
    elif expected_failed:
        assert "report" not in a.read_state(directory / "report-600001.SH.json")["result"]


def test_unchanged_reports_remain_readable_and_reusable(tmp_path, monkeypatch):
    previous = []
    calls = []
    for index in range(3):
        directory = tmp_path / str(index)
        directory.mkdir()
        frozen, state, summary, packet, report, review = research_case(directory)

        def model(directory, label, *args):
            calls.append(label)
            return review if label.endswith("verify") else report

        monkeypatch.setattr(d, "model_call", model)
        d.research_batch(directory, frozen, summary, state, previous, a.Budget(50, 300), 5)
        result = a.read_state(directory / "report-600001.SH.json")["result"]
        assert result["evidence_review"] == "passed"
        assert result["report"] == report and result["evidence"] == packet
        assert result["status"] == ("scoped_complete" if index == 0 else "unchanged")
        markdown = (directory / "report-600001.SH.md").read_text()
        assert "业务判断" in markdown and "原文/模型验证未完成" not in markdown
        previous = [(directory, state)]
    assert len(calls) == 2


def test_resume_after_announcement_publish_reuses_frozen_list(tmp_path, monkeypatch):
    frozen, state, summary, packet, report, review = research_case(tmp_path)
    (tmp_path / "evidence-600001.SH.json").unlink()
    calls = []
    monkeypatch.setattr(d.OfficialSource, "announcements", lambda *_: calls.append("fetch") or [])
    monkeypatch.setattr(d, "select_documents", lambda *_: [{}])
    monkeypatch.setattr(
        d, "extract_document", lambda *_: (_ for _ in ()).throw(a.BudgetStop("REQUEST_BUDGET"))
    )
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 300), 5)
    assert state["cursor"] == 0 and state["research"] == {}
    monkeypatch.setattr(d, "now_iso", lambda: "2026-10-07T10:00:00+08:00")
    monkeypatch.setattr(d, "extract_document", lambda *_: {})
    monkeypatch.setattr(d, "evidence_packet", lambda *_: packet)
    monkeypatch.setattr(
        d,
        "model_call",
        lambda directory, label, *args: review if label.endswith("verify") else report,
    )
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 300), 5)
    assert calls == ["fetch"] and state["cursor"] == 1
    assert a.read_state(tmp_path / "report-600001.SH.json")["result"]["evidence_review"] == "passed"


@pytest.mark.parametrize("malformed", [None, [], "not a fact", {"id": []}])
def test_malformed_model_facts_fail_closed(malformed):
    packet, report = packet_and_report()
    review = {"facts": [malformed, *[{"id": f["id"]} for f in report["facts"][1:]]]}
    with pytest.raises(ScreenError):
        d.validate_review(review, report)
    report["facts"][0] = malformed
    with pytest.raises(ScreenError):
        d.validate_report(report, packet)


@pytest.mark.parametrize(
    "claim, sign",
    [
        ("本期营业收入234万元", "-"),
        ("本期营业收入1,234万元，增长10%", "-"),
        ("本期营业收入1,234万元，增长10%", "\u2212"),
    ],
)
def test_numeric_substrings_and_sign_changes_are_not_evidence(claim, sign):
    packet, report = packet_and_report()
    quote = f"合并口径本期营业收入1,234万元，同比增长{sign}10%。"
    packet["documents"][0]["pages"][0]["text"] += quote
    report["facts"][0].update(quote=quote, claim=quote)
    d.validate_report(report, packet)
    report["facts"][0]["claim"] = claim
    with pytest.raises(ScreenError, match="Numeric"):
        d.validate_report(report, packet)


@pytest.mark.parametrize("length,accepted", [(14, False), (15, True), (1600, True), (1601, False)])
def test_verbatim_citation_length_boundary_is_explicit(length, accepted):
    packet, report = packet_and_report()
    quote = "原" * length
    packet["documents"][0]["pages"][0]["text"] += quote
    report["facts"][0].update(quote=quote, claim=quote)
    if accepted:
        d.validate_report(report, packet)
    else:
        with pytest.raises(ScreenError, match="Citation length"):
            d.validate_report(report, packet)


@pytest.mark.parametrize(
    "quote,claim",
    [
        ("本期比上年同期增减(%)：-1.21，原文比较口径一致。", "本期较上年同期下降1.21%。"),
        (
            "2026年半年度报告，上年同期营业收入及本期比较数据。",
            "2026年上半年较2025年上半年营业收入变化。",
        ),
    ],
)
def test_table_units_and_relative_periods_do_not_authorize_new_numeric_literals(quote, claim):
    packet, report = packet_and_report()
    packet["documents"][0]["pages"][0]["text"] += quote
    report["facts"][0].update(quote=quote, claim=quote)
    d.validate_report(report, packet)
    report["facts"][0]["claim"] = claim
    with pytest.raises(ScreenError, match="Numeric"):
        d.validate_report(report, packet)


@pytest.mark.parametrize("period, accepted", [("2025-12-31", True), ("2025-06-30", False)])
def test_pdf_identity_checks_report_kind_not_only_year(tmp_path, monkeypatch, period, accepted):
    source = SimpleNamespace(request=lambda *_: b"%PDF-offline")
    monkeypatch.setattr(
        d.subprocess,
        "run",
        lambda args, **kwargs: SimpleNamespace(
            stdout="Pages: 1\n"
            if args[0] == "pdfinfo"
            else "600001 合成 2025 年年度报告，合并财务报表及其他正文\f"
        ),
    )
    document = {"url": "unused", "code": "600001.SH", "official_name": "合成", "period": period}
    if accepted:
        assert d.extract_document(source, document, tmp_path)["page_count"] == 1
    else:
        with pytest.raises(ScreenError, match="identity"):
            d.extract_document(source, document, tmp_path)


@pytest.mark.parametrize(
    "page,code_line,page_name,header,accepted",
    [
        (1, "600001", "", None, True),
        (1, "16000010", "", None, False),
        (1, "600 001", "", None, False),
        (6, "600001", "", None, True),
        (7, "股票代码:600001", "合成", None, True),
        (12, "股票代码" + " " * 18 + "600001", "合成", None, True),
        (16, "证券代码：600001", "合成", None, True),
        (17, "股票代码600001", "合成", None, False),
        (12, "600001", "合成", None, False),
        (12, "股票代码16000010", "合成", None, False),
        (12, "股票代码600002", "合成", None, False),
        (12, "股票代码:参照公司600001", "合成", None, False),
        (12, "股票代码" + " " * 64 + "600001", "合成", None, True),
        (12, "股票代码" + " " * 65 + "600001", "合成", None, False),
        (12, "股票代码600001", "其他", None, False),
        (12, "股票代码600001", "合成", "其他2025年年度报告及完整财务正文", False),
        (12, "股票代码600001", "合成", "合成2024年年度报告及完整财务正文", False),
        (12, "股票代码600001", "合成", "合成2025年半年度报告及完整财务正文", False),
    ],
)
def test_pdf_code_identity_is_exact_and_late_label_is_bounded(
    tmp_path, monkeypatch, page, code_line, page_name, header, accepted
):
    pages = ["完整的独立正文和财务说明，不含证券简称或编号。"] * 17
    pages[0] = header or "合成2025年年度报告及完整的合并财务正文。"
    pages[page - 1] += f"\n{page_name}\n{code_line}"
    monkeypatch.setattr(
        d.subprocess,
        "run",
        lambda args, **kwargs: SimpleNamespace(
            stdout="Pages: 17\n" if args[0] == "pdfinfo" else "\f".join(pages) + "\f"
        ),
    )
    document = {
        "url": "unused",
        "code": "600001.SH",
        "official_name": "合成",
        "period": "2025-12-31",
    }
    source = SimpleNamespace(request=lambda *_: b"%PDF-offline")
    if accepted:
        assert d.extract_document(source, document, tmp_path)["page_count"] == 17
    else:
        with pytest.raises(ScreenError, match="identity"):
            d.extract_document(source, document, tmp_path)


def test_model_subprocess_success_and_response_resume(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    (runtime / ".venv/bin").mkdir(parents=True)
    (runtime / ".venv/bin/python").touch()
    monkeypatch.setenv("AUTOMATION_HERMES_RUNTIME", str(runtime))
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-secret")
    monkeypatch.setenv("STATE_DIR", "/must-not-be-used")
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        assert "TUSHARE_TOKEN" not in kwargs["env"] and "STATE_DIR" not in kwargs["env"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "tool_count": 0,
                    "model": "m",
                    "provider": "p",
                    "text": '```json\n{"ok":true}\n```',
                    "usage": {},
                }
            ),
        )

    monkeypatch.setattr(d.subprocess, "run", command)
    state = {"model_calls": 0}
    for _ in range(2):
        assert d.model_call(
            tmp_path,
            "one",
            "public input",
            {"provider": "p", "model": "m"},
            state,
            a.Budget(2, 300),
        ) == {"ok": True}
    assert state["model_calls"] == 1 and len(calls) == 1


def test_model_generation_resumes_across_packet_serialization(tmp_path, monkeypatch):
    frozen, state, summary, packet, report, review = research_case(tmp_path)
    (tmp_path / "evidence-600001.SH.json").unlink()
    runtime = tmp_path / "runtime"
    (runtime / ".venv/bin").mkdir(parents=True)
    (runtime / ".venv/bin/python").touch()
    monkeypatch.setenv("AUTOMATION_HERMES_RUNTIME", str(runtime))
    monkeypatch.setattr(d.OfficialSource, "announcements", lambda *_: [])
    monkeypatch.setattr(d, "select_documents", lambda *_: [])
    monkeypatch.setattr(d, "evidence_packet", lambda *_: packet)
    original_call, calls = d.model_call, []
    interrupted = False

    def model(*args):
        nonlocal interrupted
        if args[1].endswith("verify") and not interrupted:
            interrupted = True
            raise a.BudgetStop("TIME_BUDGET")
        return original_call(*args)

    def command(args, **kwargs):
        calls.append(args[-1])
        answer = review if "verify" in args[-1] else report
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "tool_count": 0,
                    "model": "m",
                    "provider": "p",
                    "text": json.dumps(answer, ensure_ascii=False),
                    "usage": {},
                }
            ),
        )

    monkeypatch.setattr(d.subprocess, "run", command)
    monkeypatch.setattr(d, "model_call", model)
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 300), 5)
    assert state["cursor"] == 0 and len(calls) == 1
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 300), 5)
    assert state["cursor"] == 1 and len(calls) == state["model_calls"] == 2
    assert a.read_state(tmp_path / "report-600001.SH.json")["result"]["evidence_review"] == "passed"
