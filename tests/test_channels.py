"""Channel accounting and verified MCP projections use synthetic local batches only."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient

from a_stock_tracker import research_service as service
from a_stock_tracker.automation import read_state, save, save_state
from a_stock_tracker.contracts import (
    ErrorBody,
    Fact,
    PriceReference,
    Quality,
    RankingPage,
    ReportList,
    ReportView,
    Section,
    ServiceInfo,
)
from a_stock_tracker.disclosures import TEMPLATE, structured_report
from a_stock_tracker.research import SHANGHAI, ScreenError
from a_stock_tracker.research_client import ResearchClient, ResearchRejected, ResearchUnavailable
from tests.test_research_service import payload, screen_checkpoint_fixture

TOKENS = {"web": "w" * 32, "mcp": "m" * 32}
LIMIT_MESSAGE = "MCP 渠道今日提交次数已用完（每日 3 次），明日再试或在网页提交。"
CURSOR_MESSAGE = "分页游标与当前批次或过滤条件不符"


def auth(channel="mcp"):
    return {"Authorization": "Bearer " + TOKENS[channel]}


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    monkeypatch.setenv("RESEARCH_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_MODEL", "synthetic")
    monkeypatch.setattr(service, "model_command", lambda: ["synthetic"])

    class FinishedThread:
        def __init__(self, target, args, daemon):
            self.args = args

        def start(self):
            directory, state, handle = self.args
            try:
                state.update(status="failed", usage={"model_calls": None})
                save_state(directory / "request.json", state)
            finally:
                handle.close()

    monkeypatch.setattr(service, "Thread", FinishedThread)


def record(store, request, **fields):
    state = {"id": request["id"], "request": request, "status": "failed", **fields}
    directory = store.directory(request["id"])
    directory.mkdir(mode=0o700)
    save_state(directory / "request.json", state)
    return state


@pytest.mark.parametrize(
    "tokens",
    [
        {},
        {"mcp": TOKENS["mcp"]},
        {"web": "short"},
        {"web": TOKENS["web"], "mcp": "short"},
        {"web": "REPLACE_" + "x" * 32},
        {"web": TOKENS["web"], "mcp": "REPLACE_" + "x" * 32},
        {"web": TOKENS["web"], "mcp": TOKENS["web"]},
    ],
)
def test_invalid_channel_tokens_rejected(tmp_path, tokens):
    with pytest.raises(ValueError):
        service.create_service(tmp_path / "service", tokens)
    assert not (tmp_path / "service").exists()


def test_auth_checks_every_token_and_optional_channel(tmp_path, monkeypatch, ready):
    calls = []
    compare = service.hmac.compare_digest

    def checked(left, right):
        calls.append(right)
        return compare(left, right)

    monkeypatch.setattr(service.hmac, "compare_digest", checked)
    with TestClient(service.create_service(tmp_path / "both", TOKENS)) as client:
        for channel in TOKENS:
            calls.clear()
            response = client.get("/info", headers=auth(channel))
            assert response.status_code == 200
            assert response.json()["channel"] == channel
            assert calls == [("Bearer " + token).encode() for token in TOKENS.values()]
        calls.clear()
        denied = client.get("/info", headers={"Authorization": "Bearer " + "u" * 32})
        assert denied.status_code == 403 and len(calls) == 2
        assert ErrorBody.model_validate(denied.json()).detail == "研究服务授权失败"
    with TestClient(service.create_service(tmp_path / "web", {"web": TOKENS["web"]})) as client:
        assert client.get("/info", headers=auth()).status_code == 403
        assert client.get("/info", headers=auth("web")).status_code == 200


@pytest.mark.parametrize("mcp_enabled", [False, True])
def test_main_loads_channel_tokens(tmp_path, monkeypatch, ready, mcp_enabled):
    monkeypatch.setenv("RESEARCH_SERVICE_ROOT", str(tmp_path / "service"))
    monkeypatch.setenv("RESEARCH_SERVICE_TOKEN", TOKENS["web"])
    monkeypatch.delenv("RESEARCH_SERVICE_SOCKET", raising=False)
    if mcp_enabled:
        monkeypatch.setenv("RESEARCH_SERVICE_MCP_TOKEN", TOKENS["mcp"])
    else:
        monkeypatch.delenv("RESEARCH_SERVICE_MCP_TOKEN", raising=False)
    apps = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: apps.append(app))
    service.main()
    with TestClient(apps[0]) as client:
        assert client.get("/info", headers=auth("web")).status_code == 200
        assert client.get("/info", headers=auth()).status_code == (200 if mcp_enabled else 403)


def test_channel_records_limit_idempotency_and_info(tmp_path, ready):
    root = tmp_path / "service"
    with TestClient(service.create_service(root, TOKENS)) as client:
        store = service.ResearchStore(root)
        info = ServiceInfo.model_validate(client.get("/info", headers=auth()).json())
        assert (info.channel, info.channel_remaining, info.busy, info.rule) == (
            "mcp",
            3,
            False,
            TEMPLATE,
        )
        assert service.quote(info, "financial") == service.quote(
            service.request_info(), "financial"
        )
        held = store.lease()
        try:
            assert client.get("/info", headers=auth()).json()["busy"] is True
        finally:
            held.close()
        for index, mode in enumerate(("financial", "swing", "screen")):
            request = payload(mode)
            response = client.post("/runs", json=request, headers=auth())
            assert response.status_code == 200
            state = response.json()
            assert state["channel"] == "mcp" and "channel" not in state["request"]
            assert state["request"] == request and state["usage"]["model_calls"] is None
            assert read_state(store.directory(request["id"]) / "request.json") == state
            assert client.post("/runs", json=request, headers=auth()).json() == state
            assert store.channel_used() == index + 1
        info = client.get("/info", headers=auth()).json()
        assert info["channel_remaining"] == 0
        # Idempotency still precedes quote, availability, limit and busy checks.
        with store.lease():
            assert client.post("/runs", json=request, headers=auth()).json() == state
            changed = {**request, "codes": ["600519.SH"]}
            assert client.post("/runs", json=changed, headers=auth()).status_code == 400
            bad_quote = payload()
            bad_quote["quote"]["requests"] += 1
            assert (
                client.post("/runs", json=bad_quote, headers=auth()).json()["code"]
                == "quote_changed"
            )
            denied = client.post("/runs", json=payload(), headers=auth())
            assert denied.status_code == 429
            assert ErrorBody.model_validate(denied.json()).model_dump() == {
                "detail": LIMIT_MESSAGE,
                "code": "channel_limit",
            }
        for _ in range(4):
            accepted = client.post("/runs", json=payload(), headers=auth("web"))
            assert accepted.status_code == 200 and accepted.json()["channel"] == "web"
        assert client.get("/info", headers=auth("web")).json()["channel_remaining"] is None
        assert store.channel_used() == 3


def test_limit_uses_shanghai_date_and_legacy_records_are_web(tmp_path, monkeypatch, ready):
    now = datetime(2026, 10, 15, 16, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(service, "datetime", Clock)
    root = tmp_path / "service"
    with TestClient(service.create_service(root, TOKENS)) as client:
        store = service.ResearchStore(root)
        record(store, payload(), channel="mcp", created_at="2026-10-15T15:59:59+00:00")
        record(store, payload(), channel="mcp", created_at="2026-10-15T16:00:00+00:00")
        record(store, payload(), created_at=now.isoformat())
        assert store.channel_used() == 1
        assert client.get("/info", headers=auth()).json()["channel_remaining"] == 2
        for _ in range(2):
            assert client.post("/runs", json=payload(), headers=auth()).status_code == 200
        assert client.post("/runs", json=payload(), headers=auth()).status_code == 429
        now += timedelta(days=1)
        assert client.get("/info", headers=auth()).json()["channel_remaining"] == 3
        assert client.post("/runs", json=payload(), headers=auth()).status_code == 200


def test_same_day_screen_reuse_counts_as_acceptance(tmp_path, monkeypatch, ready):
    store, original, _ = screen_checkpoint_fixture(tmp_path)
    monkeypatch.setattr(service, "Thread", lambda **_: pytest.fail("reuse started execution"))
    with TestClient(service.create_service(store.root, TOKENS)) as client:
        for used in range(1, 4):
            request = payload("screen")
            response = client.post("/runs", json=request, headers=auth())
            assert response.status_code == 200
            state = response.json()
            assert state["reused_from"] == original["id"] and state["channel"] == "mcp"
            assert state["request"] == request
            assert client.post("/runs", json=request, headers=auth()).json() == state
            assert store.channel_used() == used
        assert client.post("/runs", json=payload("screen"), headers=auth()).status_code == 429
        assert client.post("/runs", json=payload("screen"), headers=auth("web")).status_code == 200


def test_concurrent_submissions_never_exceed_limit(tmp_path, ready):
    root = tmp_path / "service"
    gate = Barrier(8)
    with TestClient(service.create_service(root, TOKENS)) as client:

        def submit(_):
            request = payload()
            gate.wait(timeout=5)
            return client.post("/runs", json=request, headers=auth()).status_code

        with ThreadPoolExecutor(max_workers=8) as workers:
            statuses = list(workers.map(submit, range(8)))
        assert sorted(statuses) == [200] * 3 + [429] * 5
        assert service.ResearchStore(root).channel_used() == 3


def saved_report(
    store, number, *, status="completed", review="passed", code="600519.SH", rule=TEMPLATE
):
    request = {**payload("financial"), "id": f"{number:032x}", "codes": [code]}
    finished = datetime(2026, 10, 1, tzinfo=SHANGHAI) + timedelta(hours=number)
    state = record(store, request, status=status, created_at=finished.isoformat())
    if number % 2:
        state["finished_at"] = finished.isoformat()
        save_state(store.directory(request["id"]) / "request.json", state)
    row = {
        "code": code,
        "name": "合成公司",
        "industry": "合成行业",
        "status": "qualified",
        "reason": "ELIGIBLE",
        "pb": 1.0,
        "roe_mean": 10.0,
        "annual_roes": [{"period": "2025-12-31"}],
    }
    run = store.directory(request["id"]) / "batch" / "run-synthetic"
    run.mkdir(parents=True, mode=0o700)
    freeze = save(
        run / "universe.json",
        {"rows": [row], "mode": "sample", "sample": [code], "data_date": "2026-10-01"},
    )
    packet = {
        "code": code,
        "status": "scoped_complete",
        "evidence_review": review,
        "evidence": {
            "template": rule,
            "documents": [{"id": "D1", "period": "2025-12-31", "announced": "2026-04-01"}],
            "omitted_pages": [],
        },
        "report": {"gaps": ["合成缺口"]},
    }
    report_sha = save(
        run / f"report-{code}.json",
        {"sha256": service.digest({"result": packet}), "state": {"result": packet}},
    )
    save_state(run / "state.json", {"freeze": freeze, "scan": {}, "research": {code: report_sha}})
    return request["id"], run


def test_find_reports_verified_passed_newest_twenty_and_rules(tmp_path, ready):
    root = tmp_path / "service"
    receiver = service.create_service(root, TOKENS)
    store = service.ResearchStore(root)
    for number in range(1, 23):
        saved_report(
            store,
            number,
            status="partial" if number % 2 else "completed",
            rule="cited-machine-research-v1" if number % 2 else TEMPLATE,
        )
    saved_report(store, 23, status="failed")
    saved_report(store, 24, review="failed")
    saved_report(store, 25, code="000001.SZ")
    damaged_id, damaged = saved_report(store, 26)
    (damaged / "report-600519.SH.json").write_text("{}")
    with TestClient(receiver) as client:
        response = client.get("/reports", params={"code": "600519.SH"}, headers=auth())
        assert response.status_code == 200
        reports = ReportList.model_validate(response.json()).reports
        assert [report.run_id for report in reports] == [f"{i:032x}" for i in range(22, 2, -1)]
        assert damaged_id not in [report.run_id for report in reports]
        assert {report.rule for report in reports} == {"cited-machine-research-v1", TEMPLATE}
        assert all(report.code == "600519.SH" and report.gap_count == 1 for report in reports)
        assert reports[0].documents == [{"period": "2025-12-31", "announced": "2026-04-01"}]
        assert all(report.omitted_pages == 0 and report.quality is None for report in reports)
        assert (
            client.get("/reports", params={"code": "600001.SH"}, headers=auth()).json()["reports"]
            == []
        )
        for code in ("300001.SZ", "600519", "../secret", ""):
            assert client.get("/reports", params={"code": code}, headers=auth()).status_code == 400


def test_reports_sort_instants_across_timezones_and_skip_failed_reads(tmp_path, monkeypatch):
    store = service.ResearchStore(tmp_path / "service")
    first, _ = saved_report(store, 1)
    later, _ = saved_report(store, 2)
    for identifier, stamp in (
        (first, "2026-10-16T00:00:00+08:00"),
        (later, "2026-10-15T16:30:00+00:00"),
    ):
        path = store.directory(identifier) / "request.json"
        state = read_state(path)
        state["finished_at"] = stamp
        save_state(path, state)
    real_result = store.result
    failures = {}
    for number, error in enumerate((OSError, ValueError, ScreenError), 3):
        identifier, _ = saved_report(store, number)
        failures[identifier] = error

    def result(directory, mode):
        if directory.name in failures:
            raise failures[directory.name]("synthetic damaged batch")
        return real_result(directory, mode)

    monkeypatch.setattr(store, "result", result)
    assert [report["run_id"] for report in store.find_reports("600519.SH")["reports"]] == [
        later,
        first,
    ]


@pytest.fixture
def large_screen(tmp_path):
    store, request, run = screen_checkpoint_fixture(tmp_path)
    row = read_state(next(run.glob("scan-*.json")))["result"]
    frozen = service.read_result(run / "universe.json")
    # Long synthetic names cross the response ceiling with only four pages.
    frozen["rows"] = [
        {
            **row,
            "code": f"{600000 + i:06d}.SH",
            "name": f"company-{i:03d}-" + "x" * 26000,
            "industry": "A" if i < 75 else "B",
            "pb": float(i + 1),
        }
        for i in range(151)
    ]
    state = read_state(run / "state.json")
    state.update(freeze=save(run / "universe.json", frozen), scan={})
    save_state(run / "state.json", state)
    return store, request, run


def test_ranking_pages_full_verified_result_and_filters(large_screen):
    store, request, run = large_screen
    path = "/runs/" + request["id"]
    baseline = store.result(run.parent.parent, "screen")
    assert baseline["truncated"]
    assert len(json.dumps(baseline, ensure_ascii=False).encode()) < 3_900_000
    assert sum(len(group["ranking"]) for group in baseline["groups"]) < 151
    with TestClient(service.create_service(store.root, TOKENS)) as client:
        old = client.get(path, headers=auth())
        assert old.status_code == 200
        assert json.dumps(old.json()["result"], ensure_ascii=False) == json.dumps(
            baseline, ensure_ascii=False
        )
        items, cursor = [], None
        while True:
            response = client.get(
                path + "/ranking",
                params={} if cursor is None else {"cursor": cursor},
                headers=auth(),
            )
            assert response.status_code == 200
            page = RankingPage.model_validate(response.json())
            assert page.offset == len(items) and page.total == 151
            assert (
                page.coverage == baseline["coverage"]
                and page.prefilter == baseline["coverage"]["prefilter"]
            )
            assert page.industries == ["A", "B"]
            assert len(page.items) == min(50, 151 - len(items))
            items.extend(page.items)
            if page.is_last:
                assert page.next_cursor is None
                break
            assert page.next_cursor is not None
            cursor = page.next_cursor
        assert [row["code"] for row in items] == [f"{600000 + i:06d}.SH" for i in range(151)]
        assert len({row["code"] for row in items}) == 151
        assert all(
            set(row)
            == {
                "industry",
                "code",
                "name",
                "pb",
                "roe_mean",
                "total_mv",
                "pb_rank",
                "roe_rank",
                "order",
            }
            for row in items
        )
        for params, expected in [
            ({"industry": "B", "query": "company-100"}, ["600100.SH"]),
            ({"query": "600100"}, ["600100.SH"]),
            ({"industry": "unknown"}, []),
            ({"industry": "A", "query": "company-100"}, []),
        ]:
            page = client.get(path + "/ranking", params=params, headers=auth()).json()
            assert [row["code"] for row in page["items"]] == expected
            assert page["total"] == len(expected) and page["is_last"]
        assert (
            client.get(path + "/ranking", params={"query": "x" * 21}, headers=auth()).status_code
            == 400
        )
        alias = payload("screen")
        record(store, alias, status="partial", reused_from=request["id"])
        page = client.get("/runs/" + alias["id"] + "/ranking", headers=auth()).json()
        assert page["run_id"] == alias["id"] and page["total"] == 151
        # Every page verifies the original frozen batch again.
        (run / "universe.json").write_text("{}")
        assert (
            client.get(path + "/ranking", params={"cursor": cursor}, headers=auth()).status_code
            == 400
        )


def test_ranking_rejects_bad_cursors_and_non_screen(large_screen):
    store, request, run = large_screen
    path = "/runs/" + request["id"] + "/ranking"
    with TestClient(service.create_service(store.root, TOKENS)) as client:
        first = client.get(path, headers=auth()).json()
        cursor = first["next_cursor"]
        original = json.loads(base64.urlsafe_b64decode(cursor))
        assert original == {
            "freeze": read_state(run / "state.json")["freeze"],
            "industry": None,
            "query": None,
            "offset": 50,
        }
        params_list = [
            {"cursor": cursor, "industry": "A"},
            {"cursor": cursor, "query": "company"},
            *({"cursor": value} for value in ("garbled!", "", "bnVsbA==", "W10=")),
        ]
        for change in (
            {"freeze": "changed"},
            {"offset": -1},
            {"offset": True},
            {"offset": "50"},
            {"offset": 152},
        ):
            changed = base64.urlsafe_b64encode(json.dumps({**original, **change}).encode()).decode()
            params_list.append({"cursor": changed})
        for params in params_list:
            response = client.get(path, params=params, headers=auth())
            assert response.status_code == 400
            assert ErrorBody.model_validate(response.json()).detail == CURSOR_MESSAGE
        for mode, status in (
            ("financial", "completed"),
            ("screen", "failed"),
            ("screen", "running"),
        ):
            request = payload(mode)
            record(store, request, status=status)
            response = client.get("/runs/" + request["id"] + "/ranking", headers=auth())
            assert response.status_code == 400
            assert response.json()["detail"] == "该研究没有可分页的初筛排名"


def test_client_report_ranking_models_params_and_limit(large_screen, monkeypatch, ready):
    store, request, _ = large_screen
    with TestClient(service.create_service(store.root, TOKENS)) as receiver:
        original = httpx.Client
        seen = []

        def forward(incoming):
            seen.append(incoming)
            answer = receiver.request(
                incoming.method,
                str(incoming.url),
                content=incoming.content,
                headers={"Authorization": incoming.headers["Authorization"]},
            )
            return httpx.Response(
                answer.status_code,
                content=answer.content,
                headers={"content-type": answer.headers.get("content-type", "")},
            )

        monkeypatch.setattr(
            httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(forward))
        )
        client = ResearchClient("http://testserver", TOKENS["mcp"])
        assert isinstance(client.find_reports("600519.SH"), ReportList)
        page = client.ranking(request["id"], industry="B", query="company")
        assert isinstance(page, RankingPage) and page.total == 76
        assert dict(seen[-1].url.params) == {"industry": "B", "query": "company"}
        page = client.ranking(request["id"], industry="B", query="company", cursor=page.next_cursor)
        assert page.offset == 50 and page.is_last and len(page.items) == 26
        for _ in range(3):
            assert client.submit(payload()).channel == "mcp"
        with pytest.raises(ResearchRejected) as error:
            client.submit(payload())
        assert str(error.value) == LIMIT_MESSAGE
        for identifier in ("../path", "A" * 32, "a" * 31):
            with pytest.raises(ResearchUnavailable, match="请求编号不正确"):
                client.ranking(identifier)


@pytest.mark.parametrize(
    "method,path",
    [("find_reports", "/reports?code=600519.SH"), ("ranking", "/runs/" + "a" * 32 + "/ranking")],
)
def test_read_response_validation_is_not_acceptance_unknown(tmp_path, monkeypatch, method, path):
    monkeypatch.setattr(service.ResearchStore, method, lambda *args: {"invalid": True})
    with TestClient(service.create_service(tmp_path / "service", TOKENS)) as client:
        response = client.get(path, headers=auth())
        assert response.status_code == 400
        assert ErrorBody.model_validate(response.json()).code == "invalid_request"


def test_projection_contracts_preserve_missing_and_extra_fields():
    fact = {
        "id": "F1",
        "claim": "fact",
        "title": "annual",
        "page": 1,
        "period": "2025-12-31",
        "announced": "2026-04-01",
        "quote": "quote",
        "url": "https://example.invalid",
        "future": True,
    }
    report = structured_report({"code": "600519.SH", "status": "failed"})
    assert report["rule"] is None
    assert ReportView.model_validate(report).model_dump(exclude_unset=True) == report
    for model, value in (
        (Fact, fact),
        (Section, {"text": "text", "facts": ["F1"], "future": True}),
        (Quality, {"generated": 1, "passed": 1, "future": True}),
        (PriceReference, {"code": "600519.SH", "status": "gap", "levels": None, "future": True}),
    ):
        assert model.model_validate(value).model_dump(exclude_unset=True) == value
    report.update(
        evidence_review="passed",
        rule=TEMPLATE,
        quality={"generated": 1, "passed": 1},
        analysis={"business": {"text": "text", "facts": ["F1"]}},
        facts=[fact],
    )
    assert ReportView.model_validate(report).model_dump(exclude_unset=True) == report
