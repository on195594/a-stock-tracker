"""Outer research contracts preserve the existing wire and storage shapes."""

from copy import deepcopy

import pytest

from a_stock_tracker.research_service import quote, request_info

GOLDEN = {
    "info": {
        "demo": False,
        "modes": {
            "screen": {
                "title": "财报初筛找候选",
                "requests": 1500,
                "seconds": 3600,
                "reports": 0,
                "prefilter": {"rule": "pb-mv-screen-v1", "pb_max": 3, "total_mv_min_yi": 50},
                "available": True,
            },
            "financial": {
                "title": "单家公司财报研究",
                "pipeline": "evidence-first-v1",
                "requests": 100,
                "seconds": 1800,
                "reports": 1,
                "available": True,
            },
            "swing": {
                "title": "价格纪律参考",
                "requests": 20,
                "seconds": 900,
                "reports": 0,
                "available": True,
            },
        },
        "model": {"provider": "synthetic", "model": "synthetic"},
    },
    "quotes": {
        "screen": {
            "title": "财报初筛找候选",
            "requests": 1500,
            "seconds": 3600,
            "reports": 0,
            "prefilter": {"rule": "pb-mv-screen-v1", "pb_max": 3, "total_mv_min_yi": 50},
            "model": None,
        },
        "financial": {
            "title": "单家公司财报研究",
            "pipeline": "evidence-first-v1",
            "requests": 100,
            "seconds": 1800,
            "reports": 1,
            "model": {"provider": "synthetic", "model": "synthetic"},
            "context_budget": {
                "method": "utf8_bytes",
                "limits": {
                    "select": 75000,
                    "compose": 18000,
                    "review": 90000,
                    "repair": 18000,
                    "recheck": 90000,
                },
            },
            "model_budget": {
                "calls": 10,
                "local_timeout_seconds": 240,
                "output_token_hard_limit": None,
                "cost_hard_limit": None,
                "timeout_guarantees_billing_stop": False,
            },
        },
        "swing": {
            "title": "价格纪律参考",
            "requests": 20,
            "seconds": 900,
            "reports": 0,
            "model": None,
        },
    },
    "continuation": {
        "title": "财报初筛找候选",
        "requests": 24,
        "seconds": 66,
        "reports": 0,
        "prefilter": {"rule": "pb-mv-screen-v1", "pb_max": 3, "total_mv_min_yi": 50},
        "model": None,
    },
}


@pytest.fixture
def service_info(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    monkeypatch.setenv("RESEARCH_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_MODEL", "synthetic")
    monkeypatch.setenv("RESEARCH_PIPELINE", "evidence-first-v1")
    monkeypatch.setattr("a_stock_tracker.research_service.model_command", lambda: ["synthetic"])
    return deepcopy(GOLDEN["info"])


def test_golden_quotes_and_service_info(service_info):
    assert request_info() == GOLDEN["info"]
    assert set(request_info()) == {"demo", "modes", "model"}
    for mode, expected in GOLDEN["quotes"].items():
        assert quote(service_info, mode) == expected
    assert quote(service_info, "screen", 4) == GOLDEN["continuation"]


def request(mode="swing", **extra):
    return {
        "id": "a" * 32,
        "mode": mode,
        "codes": [] if mode == "screen" else ["600519.SH"],
        "confirmed": True,
        "quote": deepcopy(GOLDEN["quotes"][mode]),
        **extra,
    }


@pytest.mark.parametrize(
    "change,message",
    [
        ({"root": "/tmp/private"}, "请求格式不正确"),
        ({"id": "../x"}, "请求编号不正确"),
        ({"id": "A" * 32}, "请求编号不正确"),
        ({"id": 123}, "请求编号不正确"),
        ({"mode": "trade"}, "必须明确确认研究类型与预算"),
        ({"mode": None}, "必须明确确认研究类型与预算"),
        ({"confirmed": False}, "必须明确确认研究类型与预算"),
        ({"confirmed": 1}, "必须明确确认研究类型与预算"),
        ({"codes": ["../../secret"]}, "仅支持沪深主板代码，如 600519.SH 或 000001.SZ"),
        ({"codes": [True]}, "仅支持沪深主板代码，如 600519.SH 或 000001.SZ"),
        ({"codes": ["300001.SZ"]}, "仅支持沪深主板代码，如 600519.SH 或 000001.SZ"),
        ({"codes": "600519.SH"}, "仅支持沪深主板代码，如 600519.SH 或 000001.SZ"),
        ({"codes": ["600519.SH"] * 2}, "量价研究限一至五家；财报研究限一家；初筛不填代码"),
        ({"codes": []}, "量价研究限一至五家；财报研究限一家；初筛不填代码"),
        (
            {"codes": [f"60000{i}.SH" for i in range(6)]},
            "量价研究限一至五家；财报研究限一家；初筛不填代码",
        ),
        ({"mode": "screen"}, "量价研究限一至五家；财报研究限一家；初筛不填代码"),
        ({"mode": "financial", "codes": []}, "量价研究限一至五家；财报研究限一家；初筛不填代码"),
        (
            {"mode": "financial", "codes": ["600519.SH", "000001.SZ"]},
            "量价研究限一至五家；财报研究限一家；初筛不填代码",
        ),
        ({"from": "../x"}, "来源初筛编号不正确"),
        ({"from": 123}, "来源初筛编号不正确"),
        ({"from": None}, "来源初筛编号不正确"),
        ({"from": "A" * 32}, "来源初筛编号不正确"),
        ({"mode": "screen", "codes": [], "from": "b" * 32}, "来源初筛编号不正确"),
        ({"resume_from": "b" * 32}, "继续初筛必须指定有效的原请求编号"),
        (
            {"mode": "screen", "codes": [], "resume_from": "../x"},
            "继续初筛必须指定有效的原请求编号",
        ),
        ({"mode": "screen", "codes": [], "resume_from": 123}, "继续初筛必须指定有效的原请求编号"),
        ({"mode": "screen", "codes": [], "resume_from": None}, "继续初筛必须指定有效的原请求编号"),
        (
            {"mode": "screen", "codes": [], "resume_from": "A" * 32},
            "继续初筛必须指定有效的原请求编号",
        ),
        ({"quote": []}, "必须核对本次预算与模型"),
        ({"quote": None}, "必须核对本次预算与模型"),
        # Multiple failures pin the original first-error ordering.
        ({"from": "bad", "id": "bad", "resume_from": "bad"}, "来源初筛编号不正确"),
        ({"id": "bad", "resume_from": "bad", "mode": "bad"}, "请求编号不正确"),
        (
            {"resume_from": "bad", "mode": "bad", "confirmed": False},
            "继续初筛必须指定有效的原请求编号",
        ),
        ({"confirmed": False, "codes": None}, "必须明确确认研究类型与预算"),
        ({"codes": [], "quote": None}, "量价研究限一至五家；财报研究限一家；初筛不填代码"),
    ],
)
def test_invalid_requests_keep_messages_and_order(change, message):
    from a_stock_tracker.contracts import RunRequest
    from a_stock_tracker.research_service import validate_request

    value = request()
    value.update(change)
    with pytest.raises(ValueError) as error:
        validate_request(value)
    assert str(error.value) == message
    with pytest.raises(ValueError, match=message):
        RunRequest.model_validate(value)


@pytest.mark.parametrize("value", [None, [], {}, {"id": "a" * 32}])
def test_malformed_requests(value):
    from a_stock_tracker.research_service import validate_request

    with pytest.raises(ValueError, match="^请求格式不正确$"):
        validate_request(value)


@pytest.mark.parametrize("mode", ["screen", "financial", "swing"])
@pytest.mark.parametrize("reference", [False, True])
def test_valid_requests_round_trip(mode, reference):
    from a_stock_tracker.contracts import RunRequest
    from a_stock_tracker.research_service import validate_request

    value = request(mode)
    if reference:
        value["resume_from" if mode == "screen" else "from"] = "b" * 32
    before = deepcopy(value)
    assert RunRequest.model_validate(value).model_dump(by_alias=True, exclude_unset=True) == before
    assert validate_request(value) == before
    assert value == before


def test_quote_content_is_compared_by_receiver_without_normalization():
    from a_stock_tracker.research_service import validate_request

    for offered in ({}, {"model_budget": {"calls": 1}, "legacy": True}, {1: "unchanged"}):
        value = request(quote=offered)
        assert validate_request(value) == value


@pytest.mark.parametrize("status", ["failed", "completed", "partial"])
def test_legacy_stored_request_survives_status_and_recent(tmp_path, monkeypatch, status):
    from a_stock_tracker.automation import read_state, save_state
    from a_stock_tracker.contracts import RunList, RunState, StoredRequest
    from a_stock_tracker.research_service import ResearchStore

    store = ResearchStore(tmp_path / "store")
    old = request("financial")
    old["quote"].pop("model_budget")
    old["quote"]["legacy_quote"] = {"keep": True}
    old["legacy_request"] = ["keep"]
    state = {"id": old["id"], "status": status, "request": old, "legacy_state": True}
    path = store.directory(old["id"]) / "request.json"
    path.parent.mkdir(mode=0o700)
    save_state(path, state)
    monkeypatch.setattr(store, "result", lambda *_: {"reports": []})
    result = {"result": {"reports": []}} if status != "failed" else {}
    assert store.status(old["id"]) == {**state, **result}
    assert isinstance(RunState.model_validate(state).request, StoredRequest)
    assert RunState.model_validate(state).model_dump(by_alias=True, exclude_unset=True) == state
    recent = store.recent()
    summary = {"summary": {"reports": []}} if status != "failed" else {}
    assert recent == [{**state, **summary, "continuations": 0, "reused": False}]
    assert RunList.model_validate({"runs": recent}).model_dump(
        by_alias=True, exclude_unset=True
    ) == {"runs": recent}
    assert read_state(path) == state


def test_tolerant_response_models_preserve_unknown_fields():
    from a_stock_tracker.contracts import RunState, ServiceInfo

    state = {"id": "a" * 32, "status": "future", "new": {"value": 1}}
    assert RunState.model_validate(state).model_dump(by_alias=True, exclude_unset=True) == state
    info = deepcopy(GOLDEN["info"])
    info["new"] = True
    info["modes"]["screen"]["new"] = 2
    assert ServiceInfo.model_validate(info).model_dump(by_alias=True, exclude_unset=True) == info


def test_receiver_and_client_round_trip(tmp_path, monkeypatch, service_info):
    import httpx
    from fastapi.testclient import TestClient

    from a_stock_tracker.contracts import ErrorBody, RunState, ServiceInfo
    from a_stock_tracker.research_client import ResearchClient
    from a_stock_tracker.research_service import ResearchStore, create_service

    held = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            held.append(args[2])

        def start(self):
            pass

    monkeypatch.setattr("a_stock_tracker.research_service.Thread", DeferredThread)
    try:
        with TestClient(create_service(tmp_path / "service", {"web": "x" * 32})) as receiver:
            receiver.headers["Authorization"] = "Bearer " + "x" * 32
            original = httpx.Client

            def forward(r):
                # TestClient may be backed by httpx2; hand back a plain httpx response.
                answer = receiver.request(r.method, r.url.path, content=r.content)
                return httpx.Response(
                    answer.status_code,
                    content=answer.content,
                    headers={"content-type": answer.headers.get("content-type", "")},
                )

            monkeypatch.setattr(
                httpx,
                "Client",
                lambda **kw: original(transport=httpx.MockTransport(forward)),
            )
            client = ResearchClient("http://research", "x" * 32)
            assert isinstance(client.info(), ServiceInfo)
            assert client.info().model_dump(by_alias=True, exclude_unset=True) == {
                **service_info,
                "channel": "web",
                "channel_remaining": None,
                "busy": False,
                "rule": "cited-machine-research-v4",
            }
            value = request("screen")
            submitted = client.submit(value)
            assert isinstance(submitted, RunState)
            assert submitted.request.model_dump(by_alias=True, exclude_unset=True) == value
            raw = submitted.model_dump(by_alias=True, exclude_unset=True)
            assert client.status(value["id"]).model_dump(by_alias=True, exclude_unset=True) == {
                **raw,
                "continued_by": None,
            }
            assert client.submit(value).model_dump(by_alias=True, exclude_unset=True) == raw
            assert client.recent()[0].model_dump(by_alias=True, exclude_unset=True) == {
                **raw,
                "continued_by": None,
                "continuations": 0,
                "reused": False,
            }
            changed = request(id="b" * 32)
            changed["quote"]["requests"] += 1
            response = receiver.post("/runs", json=changed)
            assert response.status_code == 409
            assert ErrorBody.model_validate(response.json()).code == "quote_changed"
            response = receiver.get("/runs/" + "c" * 32)
            assert response.status_code == 404
            assert ErrorBody.model_validate(response.json()).code == "not_found"
            assert ResearchStore(tmp_path / "service").status(value["id"]) == {
                **raw,
                "continued_by": None,
            }
    finally:
        for lease in held:
            lease.close()


@pytest.mark.parametrize(
    "status,body,exception,message",
    [
        (
            400,
            {"code": "invalid_request", "detail": "请求不正确"},
            "ResearchRejected",
            "请求不正确",
        ),
        (403, {"code": "unauthorized", "detail": "授权失败"}, "ResearchRejected", "授权失败"),
        (404, {"code": "not_found", "detail": "没有记录"}, "ResearchRejected", "没有记录"),
        (409, {"code": "busy", "detail": "忙碌"}, "ResearchRejected", "忙碌"),
        (409, {"code": "quote_changed", "detail": "请确认"}, "ResearchQuoteChanged", "请确认"),
        (413, {"code": "too_large", "detail": "过大"}, "ResearchRejected", "过大"),
        (503, {"code": "acceptance_unknown", "detail": "未确认"}, "ResearchUnavailable", "未确认"),
        (409, {"detail": "旧接收器"}, "ResearchRejected", "旧接收器"),
        (409, {"code": "future", "detail": "兼容"}, "ResearchRejected", "兼容"),
        (409, {"code": "busy", "detail": "x" * 100}, "ResearchRejected", "x" * 100),
        (409, {"code": "busy", "detail": "x" * 101}, "ResearchUnavailable", None),
        (409, {"code": "busy", "detail": 12}, "ResearchUnavailable", None),
        (409, [], "ResearchUnavailable", None),
        (500, {"code": "service_error", "detail": "private"}, "ResearchUnavailable", None),
        (500, "internal server traceback with TUSHARE_TOKEN=secret", "ResearchUnavailable", None),
    ],
)
def test_client_error_mapping(monkeypatch, status, body, exception, message):
    import httpx

    from a_stock_tracker import research_client

    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    status, **({"text": body} if isinstance(body, str) else {"json": body})
                )
            )
        ),
    )
    client = research_client.ResearchClient("http://research", "x" * 32)
    with pytest.raises(getattr(research_client, exception)) as error:
        client.submit(request())
    assert type(error.value).__name__ == exception
    assert str(error.value) == (
        message
        if message is not None
        else "研究服务或受理状态暂不可确认。核对原请求，不自动重新收费。"
    )


@pytest.mark.parametrize("body", [{"id": "a" * 32}, {"id": "a" * 32, "status": 1}])
def test_client_submit_invalid_receipt_is_acceptance_unknown(monkeypatch, body):
    import httpx

    from a_stock_tracker.research_client import ResearchClient, ResearchUnavailable

    calls = []

    def respond(incoming):
        calls.append(incoming)
        return httpx.Response(200, json=body)

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond))
    )
    client = ResearchClient("http://research", "x" * 32)
    with pytest.raises(ValueError):
        client.submit({})
    assert not calls
    with pytest.raises(ResearchUnavailable) as error:
        client.submit(request())
    assert error.value.code == "acceptance_unknown"
    assert len(calls) == 1 and calls[0].method == "POST"


def test_demo_returns_models_and_keeps_mutable_dict_storage():
    from a_stock_tracker.contracts import RunState, ServiceInfo
    from a_stock_tracker.research_client import DemoResearchClient

    client = DemoResearchClient()
    assert isinstance(client.info(), ServiceInfo)
    value = request("screen", quote=quote(client.info(), "screen"))
    assert isinstance(client.submit(value), RunState)
    assert isinstance(client.submit(value), RunState)
    assert isinstance(client.status(value["id"]), RunState)
    assert all(isinstance(state, RunState) for state in client.recent())
    assert isinstance(client.runs[value["id"]], dict)
    client.runs[value["id"]]["message"] = "changed"
    assert client.status(value["id"]).message == "changed"


def test_real_client_returns_models_without_changing_wire_data(monkeypatch):
    import httpx

    from a_stock_tracker.contracts import RunState, ServiceInfo
    from a_stock_tracker.research_client import ResearchClient

    state = {
        "id": "a" * 32,
        "status": "running",
        "request": request(),
        "unknown": {"preserved": True},
    }
    calls = []

    def respond(incoming):
        calls.append(incoming)
        value = (
            GOLDEN["info"]
            if incoming.url.path == "/info"
            else {"runs": [state]}
            if incoming.method == "GET" and incoming.url.path == "/runs"
            else state
        )
        return httpx.Response(200, json=value)

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond))
    )
    client = ResearchClient("http://research", "x" * 32)
    info = client.info()
    assert isinstance(info, ServiceInfo)
    assert info.model_dump(by_alias=True, exclude_unset=True) == GOLDEN["info"]
    for returned in [client.submit(request()), client.status(state["id"]), *client.recent()]:
        assert isinstance(returned, RunState)
        assert returned.model_dump(by_alias=True, exclude_unset=True) == state
    assert [(call.method, call.url.path) for call in calls] == [
        ("GET", "/info"),
        ("POST", "/runs"),
        ("GET", "/runs/" + state["id"]),
        ("GET", "/runs"),
    ]


def test_submit_response_validation_failure_is_acceptance_unknown(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from a_stock_tracker.contracts import ErrorBody
    from a_stock_tracker.research_service import ResearchStore, create_service

    # The store may already have started a billed run when the response shape is wrong.
    monkeypatch.setattr(
        ResearchStore, "submit", lambda self, payload, channel="web": {"status": "running"}
    )
    with TestClient(create_service(tmp_path / "service", {"web": "x" * 32})) as receiver:
        response = receiver.post(
            "/runs", json={"id": "a" * 32}, headers={"Authorization": "Bearer " + "x" * 32}
        )
    assert response.status_code == 503
    assert ErrorBody.model_validate(response.json()).code == "acceptance_unknown"


def test_validate_request_without_validator_context_still_raises_value_error(monkeypatch):
    from pydantic import BaseModel, ValidationError

    from a_stock_tracker import research_service

    class Missing(BaseModel):
        field: int

    try:
        Missing.model_validate({})
    except ValidationError as exc:
        error = exc

    def fail(value):
        raise error

    monkeypatch.setattr(research_service.RunRequest, "model_validate", fail)
    with pytest.raises(ValueError, match="请求格式不正确"):
        research_service.validate_request({})
