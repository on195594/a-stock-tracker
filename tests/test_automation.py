from __future__ import annotations

import json
from pathlib import Path
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
            **dict.fromkeys(a.REPORT_FIELDS, 1),
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


def test_evidence_version_ignores_retrieval_but_detects_corrections(monkeypatch):
    from tests.test_evidence_pipeline import evidence_case

    packet, _ = evidence_case()
    model = {"model": "m1", "provider": "p1"}
    first = d.stable_evidence(packet, model)
    packet["retrieved_at"] = "later"
    assert d.stable_evidence(packet, model) == first
    from a_stock_tracker import evidence_pipeline as ep

    monkeypatch.setattr(ep, "PROMPTS", {**ep.PROMPTS, "select": ep.SELECT + "合同更正"})
    assert d.stable_evidence(packet, model) != first
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

    monkeypatch.setattr(d, "run_bounded", command)
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
    from a_stock_tracker import evidence_pipeline as ep
    from tests.test_evidence_pipeline import evidence_case, supported

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
    packet, report = evidence_case()
    packet.update(
        pipeline=ep.VERSION,
        template="cited-machine-research-v4",
        citation_format="evidence-bundles-v1",
        scope_gaps=[],
        omitted_pages=[],
    )
    packet["structured"] = rows[0]
    packet["documents"][0].update(
        title="合成报告", url="https://example.invalid/report.pdf", announced="2026-08-30"
    )
    state["pipeline"] = ep.VERSION
    a.save_state(
        directory / "evidence-600001.SH.json", {"freeze": state["freeze"], "packet": packet}
    )
    return frozen, state, summary, packet, report, supported(report)


def model_response(label, report, review):
    stage = label.rsplit("-", 1)[-1]
    if stage == "select":
        return {k: report[k] for k in ("code", "facts", "gaps")}
    if stage == "compose":
        return {k: report[k] for k in ("code", "analysis")}
    if stage == "repair":
        return {"facts": {}, "sentences": {}}
    return review


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
    monkeypatch.setattr(a, "freeze", lambda *_, **__: frozen)
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
    usage = a.read_result(directory / "invocation.json")
    assert usage["stop_reason"] == "request_budget"
    assert usage["requests"] == 3 and usage["remaining"] == 3
    assert state["usage"]["newly_covered"] == 3
    assert usage["seconds_used"] >= 0
    budget.stop_reason = "completed"
    budget.used = 0
    budget.limit = 10
    monkeypatch.setattr(
        sys, "argv", ["automation", "--root", str(root), "--resume", directory.name]
    )
    assert a.main() == 0
    state = a.read_state(directory / "state.json")
    assert len(state["queue"]) == state["cursor"] == 5
    assert state["usage"]["stop_reason"] == "completed"
    assert state["usage"]["newly_covered"] == 3
    assert state["usage"]["covered"] == 6 and state["usage"]["remaining"] == 0
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
        return model_response(label, report, review)

    if outcome == "review_failed":
        review["coverage"]["risk"] = False
    monkeypatch.setattr(tushare, "pro_api", lambda *_, **__: Client())
    monkeypatch.setattr(a, "freeze", lambda *_, **__: frozen)
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

        def model(directory, label, *args, _review=review, _report=report):
            calls.append(label)
            return model_response(label, _report, _review)

        monkeypatch.setattr(d, "model_call", model)
        d.research_batch(directory, frozen, summary, state, previous, a.Budget(50, 1800), 5)
        result = a.read_state(directory / "report-600001.SH.json")["result"]
        assert result["evidence_review"] == "passed"
        assert result["report"] == report and result["evidence"] == packet
        assert result["status"] == ("scoped_complete" if index == 0 else "unchanged")
        markdown = (directory / "report-600001.SH.md").read_text()
        assert "业务判断" in markdown and "原文/模型验证未完成" not in markdown
        previous = [(directory, state)]
    assert len(calls) == 3


def test_resume_after_announcement_publish_reuses_frozen_list(tmp_path, monkeypatch):
    frozen, state, summary, packet, report, review = research_case(tmp_path)
    (tmp_path / "evidence-600001.SH.json").unlink()
    calls = []
    monkeypatch.setattr(d.OfficialSource, "announcements", lambda *_: calls.append("fetch") or [])
    monkeypatch.setattr(d, "select_documents", lambda *_: [{}])
    monkeypatch.setattr(
        d, "extract_document", lambda *_: (_ for _ in ()).throw(a.BudgetStop("REQUEST_BUDGET"))
    )
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 1800), 5)
    assert state["cursor"] == 0 and state["research"] == {}
    monkeypatch.setattr(d, "now_iso", lambda: "2026-10-07T10:00:00+08:00")
    monkeypatch.setattr(d, "extract_document", lambda *_: {})
    monkeypatch.setattr(d, "evidence_packet", lambda *_: packet)
    monkeypatch.setattr(
        d,
        "model_call",
        lambda directory, label, *args: model_response(label, report, review),
    )
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 1800), 5)
    assert calls == ["fetch"] and state["cursor"] == 1
    assert a.read_state(tmp_path / "report-600001.SH.json")["result"]["evidence_review"] == "passed"


@pytest.mark.parametrize(
    "claim, sign",
    [
        ("本期营业收入234万元", "-"),
        ("本期营业收入1,234万元，增长10%", "-"),
        ("本期营业收入1,234万元，增长10%", "\u2212"),
    ],
)
def test_numeric_substrings_and_sign_changes_are_not_evidence(claim, sign):
    quote = f"合并口径本期营业收入1,234万元，同比增长{sign}10%。"
    assert not d.unquoted_numbers({"quote": quote, "claim": quote})
    assert d.unquoted_numbers({"quote": quote, "claim": claim})


@pytest.mark.parametrize(
    "quote,claim",
    [
        # A decline word may carry the sign ("下降1.21%"); an opposite direction may not.
        ("本期比上年同期增减(%)：-1.21，原文比较口径一致。", "本期较上年同期增长1.21%。"),
        (
            "2026年半年度报告，上年同期营业收入及本期比较数据。",
            "2026年上半年较2025年上半年营业收入变化。",
        ),
    ],
)
def test_table_units_and_relative_periods_do_not_authorize_new_numeric_literals(quote, claim):
    assert not d.unquoted_numbers({"quote": quote, "claim": quote})
    assert d.unquoted_numbers({"quote": quote, "claim": claim})


@pytest.mark.parametrize("period, accepted", [("2025-12-31", True), ("2025-06-30", False)])
def test_pdf_identity_checks_report_kind_not_only_year(tmp_path, monkeypatch, period, accepted):
    source = SimpleNamespace(request=lambda *_: b"%PDF-offline")
    monkeypatch.setattr(
        d,
        "run_bounded",
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
        (8, "股票代码  600001（A 股）、0300（H 股）", "合成", None, True),
        (8, "股票代码  600001（B 股）", "合成", None, False),
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
    pages[0] = header or "合成股份有限公司2025年年度报告，完整的合并财务正文。"
    pages[page - 1] += f"\n股票简称  {page_name}\n公司的中文名称  合成股份有限公司\n{code_line}"
    monkeypatch.setattr(
        d,
        "run_bounded",
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


_LATE_ISSUER_TABLE = (
    "股票简称  股称                  股票代码                  600001\n"
    "公司的中文名称  合成股份有限公司\n"
)
_LATE_COVER = "合成股份有限公司2025年年度报告，合并财务报表和完整正文。"
_LONG_COMPANY_NAME = "合" + "成" * 80 + "股份有限公司"


@pytest.mark.parametrize(
    "table,cover,accepted",
    [
        (_LATE_ISSUER_TABLE, _LATE_COVER, True),
        (_LATE_ISSUER_TABLE, "新" + _LATE_COVER, False),
        (
            _LATE_ISSUER_TABLE,
            _LATE_COVER.replace("合成股份有限公司", "合成股份有限公司集团"),
            False,
        ),
        (_LATE_ISSUER_TABLE, "相关公司：" + _LATE_COVER, False),
        (
            _LATE_ISSUER_TABLE,
            "合成股份有限公司\n2025 年年度报告，完整的合并财务正文。",
            True,
        ),
        (
            _LATE_ISSUER_TABLE,
            "其他股份有限公司2025年年度报告，完整正文。\n合成股份有限公司",
            False,
        ),
        (_LATE_ISSUER_TABLE.replace("股称", "股称制造"), _LATE_COVER, False),
        (_LATE_ISSUER_TABLE.replace("股票简称  ", ""), _LATE_COVER, False),
        (_LATE_ISSUER_TABLE.replace("股票代码", "发行编号"), _LATE_COVER, False),
        (_LATE_ISSUER_TABLE + "股票代码  600002\n", _LATE_COVER, False),
        (_LATE_ISSUER_TABLE + "股票简称  其他\n", _LATE_COVER, False),
        (_LATE_ISSUER_TABLE + "公司的中文名称  \n", _LATE_COVER, False),
        (_LATE_ISSUER_TABLE.replace("公司的中文名称", "普通提及"), _LATE_COVER, False),
        (_LATE_ISSUER_TABLE.replace("合成股份有限公司", "公司"), _LATE_COVER, False),
        (_LATE_ISSUER_TABLE.replace("合成股份有限公司", "合成股份\n有限公司"), _LATE_COVER, False),
        (
            _LATE_ISSUER_TABLE.replace("合成股份有限公司", "合成股份有限公司  附加说明"),
            _LATE_COVER,
            False,
        ),
        (_LATE_ISSUER_TABLE, _LATE_COVER.replace("合成股份有限公司", "其他股份有限公司"), False),
        (
            _LATE_ISSUER_TABLE,
            "完整独立的财务正文和事项说明合成股份\f有限公司2025年年度报告和完整财务说明。",
            False,
        ),
        (
            _LATE_ISSUER_TABLE.replace("合成股份有限公司", _LONG_COMPANY_NAME),
            _LATE_COVER.replace("合成股份有限公司", _LONG_COMPANY_NAME),
            False,
        ),
    ],
)
def test_late_issuer_table_binds_all_complete_fields_to_one_cover_page(
    tmp_path, monkeypatch, table, cover, accepted
):
    pages = ["完整独立的财务正文，不含对应证券简称或证券编号。"] * 17
    opening = cover.split("\f")
    pages[: len(opening)] = opening
    pages[11] += "\n" + table
    monkeypatch.setattr(
        d,
        "run_bounded",
        lambda args, **kwargs: SimpleNamespace(
            stdout="Pages: 17\n" if args[0] == "pdfinfo" else "\f".join(pages) + "\f"
        ),
    )
    document = {
        "url": "unused",
        "code": "600001.SH",
        "official_name": "股称",
        "period": "2025-12-31",
    }
    source = SimpleNamespace(request=lambda *_: b"%PDF-offline")
    if accepted:
        assert d.extract_document(source, document, tmp_path)["page_count"] == 17
    else:
        with pytest.raises(ScreenError, match="identity"):
            d.extract_document(source, document, tmp_path)


@pytest.mark.parametrize("engine", ["hermes", "pi"])
def test_model_subprocess_success_and_response_resume(tmp_path, monkeypatch, engine):
    runtime = tmp_path / "runtime"
    (runtime / ".venv/bin").mkdir(parents=True)
    (runtime / ".venv/bin/python").touch()
    monkeypatch.setenv("AUTOMATION_HERMES_RUNTIME", str(runtime))
    if engine == "pi":
        (runtime / "dist/core").mkdir(parents=True)
        (runtime / "dist/core/model-runtime.js").touch()
        (runtime / "auth.json").write_text("{}")
        (runtime / "provider.ts").touch()
        monkeypatch.setenv("AUTOMATION_PI_RUNTIME", str(runtime))
        monkeypatch.setenv("AUTOMATION_PI_AGENT_DIR", str(runtime))
        monkeypatch.setenv("AUTOMATION_PI_EXTENSION", str(runtime / "provider.ts"))
    else:
        monkeypatch.delenv("AUTOMATION_PI_RUNTIME", raising=False)
    monkeypatch.setenv("TUSHARE_TOKEN", "not-a-secret")
    monkeypatch.setenv("RESEARCH_SERVICE_TOKEN", "private-bridge-token")
    monkeypatch.setenv("RESEARCH_SERVICE_MCP_TOKEN", "private-mcp-token")
    monkeypatch.setenv("STATE_DIR", "/must-not-be-used")
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        assert "TUSHARE_TOKEN" not in kwargs["env"] and "STATE_DIR" not in kwargs["env"]
        assert "RESEARCH_SERVICE_TOKEN" not in kwargs["env"]
        assert "RESEARCH_SERVICE_MCP_TOKEN" not in kwargs["env"]
        assert any(f"automation_model{('_pi.mjs' if engine == 'pi' else '.py')}" in a for a in args)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "tool_count": 0,
                    "model": "m",
                    "provider": "p",
                    "text": '```JSON\n{"ok":true}\n```',
                    "usage": {},
                }
            ),
        )

    monkeypatch.setattr(d, "run_bounded", command)
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
        if args[1].endswith("review") and not interrupted:
            interrupted = True
            raise a.BudgetStop("TIME_BUDGET")
        return original_call(*args)

    def command(args, **kwargs):
        calls.append(args[-1])
        answer = model_response(Path(args[-1]).name.removesuffix(".request.json"), report, review)
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

    monkeypatch.setattr(d, "run_bounded", command)
    monkeypatch.setattr(d, "model_call", model)
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 1800), 5)
    assert state["cursor"] == 0 and len(calls) == 2
    d.research_batch(tmp_path, frozen, summary, state, [], a.Budget(50, 1800), 5)
    assert state["cursor"] == 1 and len(calls) == state["model_calls"] == 3
    assert a.read_state(tmp_path / "report-600001.SH.json")["result"]["evidence_review"] == "passed"


@pytest.mark.parametrize("outcome", ["invalid_json", "provider_failed", "timeout"])
def test_failed_model_preserves_usage_and_never_retries(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(d, "model_command", lambda: (["synthetic"], tmp_path))
    calls = []

    def command(*args, **kwargs):
        calls.append(1)
        if outcome == "timeout":
            raise d.subprocess.TimeoutExpired("synthetic", 260)
        return SimpleNamespace(
            returncode=1 if outcome == "provider_failed" else 0,
            stdout=json.dumps(
                {
                    "tool_count": 0,
                    "provider": "p",
                    "model": "m",
                    "text": "not JSON",
                    "usage": {"output": 123},
                }
            ),
        )

    monkeypatch.setattr(d, "run_bounded", command)
    state = {"model_calls": 0}
    args = (tmp_path, "failed", "public", {"provider": "p", "model": "m"}, state, a.Budget(2, 300))
    with pytest.raises((ScreenError, ValueError, d.subprocess.TimeoutExpired)):
        d.model_call(*args)
    audit = a.read_state(tmp_path / "failed.usage.json")
    assert audit["usage"] == (None if outcome == "timeout" else {"output": 123})
    with pytest.raises(ScreenError, match="refusing another charge"):
        d.model_call(*args)
    assert len(calls) == state["model_calls"] == 1
    assert not (tmp_path / "failed.response.json").exists()
    if outcome == "invalid_json":
        assert audit["unparsed_text"] == "not JSON"


def test_small_industry_completes_within_budget_without_repeating_cached_rows(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(a.time, "sleep", lambda *_: None)
    frozen, state = fixture(5)
    for row in frozen["rows"][-2:]:
        row["industry"] = "小行业"
    state["freeze"] = a.digest(frozen)
    client = Client()
    budget = a.Budget(3, 300)
    rows = a.scan(tmp_path, frozen, state, a.MarketClient(client, budget), "synthetic", budget)
    summary = a.coverage(rows, frozen)
    assert client.calls == [r["code"] for r in frozen["rows"][-2:]]
    assert summary["groups"][0]["industry"] == "小行业"
    assert len(a.candidates(summary, frozen)) == 2
    budget = a.Budget(3, 300)
    rows = a.scan(tmp_path, frozen, state, a.MarketClient(client, budget), "synthetic", budget)
    assert len(client.calls) == len(set(client.calls)) == 4
    assert sum(r["status"] == "qualified" for r in rows) == 4


def test_scan_resumes_without_rewriting_state_file_for_cached_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(a.time, "sleep", lambda *_: None)
    frozen, state = fixture(3)
    state["freeze"] = a.digest(frozen)
    client = Client()
    budget = a.Budget(10, 300)
    a.scan(tmp_path, frozen, state, a.MarketClient(client, budget), "synthetic", budget)
    state_file = tmp_path / "state.json"
    assert state_file.exists()

    saved_paths = []
    original_save_state = a.save_state

    def tracking_save_state(path, val):
        saved_paths.append(path)
        return original_save_state(path, val)

    monkeypatch.setattr(a, "save_state", tracking_save_state)
    budget2 = a.Budget(10, 300)
    rows2 = a.scan(tmp_path, frozen, state, a.MarketClient(client, budget2), "synthetic", budget2)
    assert len(rows2) == 3
    assert state_file not in saved_paths


@pytest.mark.parametrize(
    "pb,mv,status,reason",
    [
        (3, 500000, "pending", "FINANCIAL_PENDING"),
        (3.001, 600000, "excluded", "PB_ABOVE_SCREEN_MAX"),
        (1, 499999, "excluded", "TOTAL_MV_BELOW_SCREEN_MIN"),
        (1, 10**12, "pending", "FINANCIAL_PENDING"),
        *[
            (1, v, "gap", "MISSING_OR_INVALID_TOTAL_MV")
            for v in (None, True, 0, -1, "bad", float("nan"), float("inf"))
        ],
    ],
)
def test_pb_mv_prefilter_boundaries_and_units(pb, mv, status, reason):
    valuation = {"pb": pb, "total_mv": mv}
    original = a.initial_row(basic(), valuation, "2026-09-30")
    row = a.prefilter_row(original, valuation)
    assert (row["status"], row["reason"]) == (status, reason)
    assert original["status"] == "pending"  # Do not mutate historical rows.


def test_prefilter_freezes_scope_and_skips_financial_requests(tmp_path, monkeypatch):
    basics = [basic(i) for i in range(1, 5)]
    valuations = [
        {"ts_code": basics[0]["ts_code"], "pb": 3, "total_mv": 500000},
        {"ts_code": basics[1]["ts_code"], "pb": 4, "total_mv": 600000},
        {"ts_code": basics[2]["ts_code"], "pb": 1, "total_mv": 499999},
        {"ts_code": basics[3]["ts_code"], "pb": 1, "total_mv": None},
    ]
    monkeypatch.setattr(a, "target_date", lambda *_: ("2026-09-30", []))
    monkeypatch.setattr(a, "fetch_universe", lambda *_: (basics, valuations, {}))
    frozen = a.freeze(None, "synthetic", "2026-10-06", [], pb_mv_screen=True)
    assert frozen["prefilter"] == a.PB_MV_SCREEN
    state = {"freeze": a.digest(frozen), "scan": {}}
    client = Client()
    rows = a.scan(tmp_path, frozen, state, client, "synthetic", a.Budget(20, 300))
    assert client.calls == [basics[0]["ts_code"]]
    summary = a.coverage(rows, frozen)
    assert summary["prefilter_counts"] == {"pending": 1, "excluded": 2, "gap": 1}
    assert not summary["groups"]  # Unknown eligibility still blocks same-industry ranking.
    without_gap = a.coverage(rows[:3], {**frozen, "rows": frozen["rows"][:3]})
    assert without_gap["groups"][0]["top"] == [basics[0]["ts_code"]]
    legacy = a.freeze(None, "synthetic", "2026-10-06", [])
    assert "prefilter" not in legacy
    assert all(r["status"] == "pending" for r in legacy["rows"])
    with pytest.raises(ScreenError):
        a.freeze(None, "synthetic", "2026-10-06", [basics[0]["ts_code"]], pb_mv_screen=True)


def test_market_client_leaves_frequency_headroom(monkeypatch):
    sleeps = []
    monkeypatch.setattr(a.time, "sleep", sleeps.append)
    budget = a.Budget(5, 300)
    client = a.MarketClient(Client(), budget)
    client.fina_indicator(ts_code="600001.SH")
    client.fina_indicator(ts_code="600002.SH")
    assert budget.used == 2 and sleeps == [0.5, 0.5]


@pytest.mark.parametrize("frozen_filtered", [True, False])
def test_cli_rejects_changed_screen_rule_before_resume_scan(tmp_path, monkeypatch, frozen_filtered):
    import sys

    import tushare

    frozen, state = fixture(1)
    if frozen_filtered:
        frozen["prefilter"] = dict(a.PB_MV_SCREEN)
    with a.locked_root(tmp_path / "batch") as root:
        directory = root / "run-2026-10-06T10-00-00-abcdef123456"
        directory.mkdir()
        state["freeze"] = a.save(directory / "universe.json", frozen)
        a.save_state(directory / "state.json", state)
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    monkeypatch.setattr(tushare, "pro_api", lambda *_, **__: None)
    monkeypatch.setattr(a.signal, "signal", lambda *_: None)
    monkeypatch.setattr(a.signal, "alarm", lambda *_: None)
    monkeypatch.setattr(a, "scan", lambda *_: pytest.fail("mismatched scope reached scan"))
    flags = [] if frozen_filtered else ["--pb-mv-screen"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "automation",
            "--root",
            str(root),
            "--resume",
            directory.name,
            "--max-research",
            "0",
            *flags,
        ],
    )
    with pytest.raises(ScreenError, match="rule mismatch"):
        a.main()


@pytest.mark.parametrize("reason", ["error", "time_budget"])
def test_invocation_records_failure_before_freeze(tmp_path, monkeypatch, reason):
    import sys

    import tushare

    root = tmp_path / "batch"
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    monkeypatch.setattr(tushare, "pro_api", lambda *a, **kw: None)

    def fail(*args, **kwargs):
        if reason == "time_budget":
            raise a.BudgetStop("TIME_BUDGET")
        raise a.ScreenError("synthetic failure")

    monkeypatch.setattr(a, "freeze", fail)
    monkeypatch.setattr(sys, "argv", ["automation", "--root", str(root)])
    with pytest.raises((a.ScreenError, a.BudgetStop)):
        a.main()
    usage = a.read_result(root / "invocation.json")
    assert usage["stop_reason"] == reason and usage["requests"] == 0
    assert usage["seconds_used"] >= 0 and usage["remaining"] is None
