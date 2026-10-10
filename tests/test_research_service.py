"""Requests stay bounded, authenticated, idempotent and separate from Web credentials."""

import asyncio
import json
import uuid
from copy import deepcopy

import flet as ft
import httpx
import pytest
from fastapi.testclient import TestClient

from a_stock_tracker import app
from a_stock_tracker.automation import read_state, save_state
from a_stock_tracker.research_client import DemoResearchClient, ResearchClient, ResearchUnavailable
from a_stock_tracker.research_service import (
    ResearchStore,
    create_service,
    quote,
    request_info,
    validate_request,
)
from tests.test_app import AppMockPage, app_controls


def payload(mode="swing"):
    return {
        "id": uuid.uuid4().hex,
        "mode": mode,
        "codes": [] if mode == "screen" else ["600519.SH"],
        "confirmed": True,
        "quote": quote(request_info(), mode),
    }


@pytest.mark.parametrize(
    "change",
    [
        {"confirmed": False},
        {"confirmed": 1},
        {"codes": ["../../secret"]},
        {"codes": ["600519.SH"] * 2},
        {"mode": "trade"},
        {"id": "../x"},
        {"root": "/tmp/private"},
        {"codes": [True]},
        {"codes": []},
    ],
)
def test_invalid_request_refused_before_execution(change):
    with pytest.raises(ValueError):
        validate_request({**payload(), **change})


def test_private_service_auth_budget_confirmation_and_same_nonce(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    api = create_service(tmp_path / "service", "x" * 32)
    held = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            held.append(args[2])

        def start(self):
            pass

    monkeypatch.setattr("a_stock_tracker.research_service.Thread", DeferredThread)
    with TestClient(api) as client:
        assert client.get("/info").status_code == 403
        client.headers["Authorization"] = "Bearer " + "x" * 32
        assert client.post("/runs", content=b"x" * 4097).status_code == 413
        p = payload("screen")
        bad = deepcopy(p)
        bad["quote"]["requests"] += 1
        assert client.post("/runs", json=bad).status_code == 400
        first = client.post("/runs", json=p)
        assert first.status_code == 200 and first.json()["status"] == "running"
        assert client.post("/runs", json=p).json()["id"] == p["id"]
        assert len(held) == 1
        assert client.post("/runs", json=payload("screen")).status_code == 409
        assert client.get("/runs/" + p["id"]).json()["status"] == "running"
        assert client.get("/runs/not-a-nonce").status_code == 400
        held.pop().close()
        assert client.get("/runs/" + p["id"]).json()["status"] == "interrupted"
        assert client.post("/runs", json=p).json()["status"] == "interrupted"


def test_service_rejects_old_private_workspaces_symlinks_and_bad_tokens(tmp_path):
    root = tmp_path / "old"
    root.mkdir(mode=0o700)
    (root / "history.sqlite").write_text("historical data")
    with pytest.raises(Exception):
        ResearchStore(root)
    linked = tmp_path / "linked"
    linked.symlink_to(root)
    with pytest.raises(Exception):
        ResearchStore(linked)
    with pytest.raises(ValueError):
        create_service(tmp_path / "new", "short")
    assert (root / "history.sqlite").read_text() == "historical data"


def test_command_is_not_shell_and_pins_explicit_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_PROVIDER", "synthetic")
    monkeypatch.setenv("RESEARCH_MODEL", "synthetic")
    store = ResearchStore(tmp_path / "root")
    p = payload()
    command = store.command(store.directory(p["id"]), p)
    assert "--latest" in command and "--resume" not in command
    assert command[command.index("--request-budget") + 1] == "20"
    assert command[command.index("--codes") + 1] == "600519.SH"
    assert "--max-research" in store.command(store.directory(p["id"]), payload("screen"))


def test_completed_results_revalidated_on_each_read_and_failure_not_success(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "root")
    p = payload()
    directory = store.directory(p["id"])
    directory.mkdir(mode=0o700)
    state = {"id": p["id"], "request": p, "status": "completed", "result": {"old": True}}
    save_state(directory / "request.json", state)
    monkeypatch.setattr(store, "result", lambda *a: {"board": {"expired": True}})
    assert store.status(p["id"])["result"] == {"board": {"expired": True}}
    state["status"] = "running"
    lease = store.lease()
    monkeypatch.setattr(store, "command", lambda *a: (_ for _ in ()).throw(OSError("synthetic")))
    store.execute(directory, state, lease)
    assert store.status(p["id"])["status"] == "failed"
    assert "synthetic" not in json.dumps(store.status(p["id"]))


def test_client_redacts_backend_errors_and_reuses_same_request(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="private credentials and traceback")

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ResearchUnavailable) as error:
        ResearchClient("http://research", "x" * 32).submit(payload())
    assert "credentials" not in str(error.value)


def test_web_consent_demo_and_late_callback_are_inert_after_logout(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)

    async def run():
        page = AppMockPage()
        await app.build_app()(page)
        try:
            entry = next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.Button) and c.content == "开始选股与研究"
            )
            await entry.on_click(None)
            start = next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.Button) and c.content == "确认启动研究"
            )
            assert start.disabled and not client.runs
            consent = next(c for c in app_controls(page) if isinstance(c, ft.Checkbox))
            consent.value = True
            consent.on_change(None)
            await start.on_click(None)
            assert len(client.runs) == 1 and page.route.startswith("/analysis/")
            await entry.on_click(None)
            assert len(client.runs) == 1  # stale generation
            await page.on_disconnect(None)
            await start.on_click(None)
            assert len(client.runs) == 1
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_late_submit_response_cannot_overwrite_logout(monkeypatch):
    import threading
    from types import SimpleNamespace

    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    original = client.submit
    started, release = threading.Event(), threading.Event()

    def delayed(value):
        accepted = original(value)
        started.set()
        assert release.wait(5)
        return accepted

    monkeypatch.setattr(client, "submit", delayed)
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)

    async def run():
        page = AppMockPage()
        await app.build_app()(page)
        entry = next(
            c
            for c in app_controls(page)
            if isinstance(c, ft.Button) and c.content == "开始选股与研究"
        )
        await entry.on_click(None)
        consent = next(c for c in app_controls(page) if isinstance(c, ft.Checkbox))
        consent.value = True
        consent.on_change(None)
        start = next(
            c
            for c in app_controls(page)
            if isinstance(c, ft.Button) and c.content == "确认启动研究"
        )
        pending = asyncio.create_task(start.on_click(None))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            page.route = "/settings"
            await page.on_route_change(SimpleNamespace(route=page.route))
            logout = next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.Button) and c.content == "退出登录"
            )
            await logout.on_click(None)
            release.set()
            await pending
            assert page.route == "/login" and len(client.runs) == 1
            assert not any(
                isinstance(c, ft.Text) and c.value == "财报筛选与研究" for c in app_controls(page)
            )
        finally:
            release.set()
            await pending
            await page.on_close(None)

    asyncio.run(run())


def test_financial_result_reads_verified_envelopes_not_unchecked_markdown(tmp_path):
    import hashlib

    from a_stock_tracker.automation import save

    store = ResearchStore(tmp_path / "service")
    directory = store.directory(uuid.uuid4().hex)
    run = directory / "batch" / "run-synthetic"
    run.mkdir(mode=0o700, parents=True)
    row = {
        "code": "600519.SH",
        "name": "合成公司",
        "industry": "合成行业",
        "status": "qualified",
        "reason": "ELIGIBLE",
        "pb": 1.0,
        "roe_mean": 10.0,
        "annual_roes": [{"period": "20251231"}],
    }
    frozen = {"rows": [row], "mode": "sample", "sample": [row["code"]], "data_date": "20260101"}
    frozen_sha = save(run / "universe.json", frozen)
    save_state(run / "scan-600519.SH.json", {"result": row})
    scan_sha = hashlib.sha256((run / "scan-600519.SH.json").read_bytes()).hexdigest()
    save_state(
        run / "report-600519.SH.json",
        {"result": {"code": row["code"], "status": "incomplete", "error": "合成证据缺口"}},
    )
    report_sha = hashlib.sha256((run / "report-600519.SH.json").read_bytes()).hexdigest()
    save_state(
        run / "state.json",
        {
            "freeze": frozen_sha,
            "scan": {row["code"]: scan_sha},
            "research": {row["code"]: report_sha},
        },
    )
    (run / "report-600519.SH.md").write_text("unverified claims")
    result = store.result(directory, "financial")
    assert result["candidates"][0]["code"] == row["code"]
    assert "合成证据缺口" in result["reports"][0]["text"]
    assert "unverified claims" not in result["reports"][0]["text"]
    (run / "report-600519.SH.json").write_text("{}")
    with pytest.raises(Exception):
        store.result(directory, "financial")


def test_publish_sync_failure_never_returns_completed_state(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "service")
    p = payload()
    directory = store.directory(p["id"])
    directory.mkdir(mode=0o700)
    state = {"id": p["id"], "request": p, "status": "running", "limits": {"seconds": 1}}
    save_state(directory / "request.json", state)
    lease = store.lease()
    monkeypatch.setattr(
        store, "command", lambda *a: (_ for _ in ()).throw(OSError("synthetic startup failure"))
    )
    monkeypatch.setattr(
        "a_stock_tracker.research_service.save_state",
        lambda *a: (_ for _ in ()).throw(OSError("synthetic sync failure")),
    )
    with pytest.raises(OSError):
        store.execute(directory, state, lease)
    assert read_state(directory / "request.json")["status"] == "running"
    handle = store.lease()  # failed finalization releases lease; no silent success
    handle.close()


def test_execute_handles_child_process_exiting_before_killpg(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "service")
    p = payload("screen")
    directory = store.directory(p["id"])
    directory.mkdir(mode=0o700)
    state = {"id": p["id"], "request": p, "status": "running", "limits": {"seconds": 1}}
    save_state(directory / "request.json", state)
    lease = store.lease()

    class MockProcess:
        pid = 123456

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: MockProcess())

    def raise_lookup(pid, sig):
        raise ProcessLookupError("process exited")

    monkeypatch.setattr("os.killpg", raise_lookup)
    monkeypatch.setattr(store, "result", lambda *a: {"coverage": {"status": "complete"}})
    store.execute(directory, state, lease)
    assert read_state(directory / "request.json")["status"] == "completed"


def test_execute_pins_cwd_to_repo_root(tmp_path, monkeypatch):
    from a_stock_tracker.research_service import REPO_ROOT

    store = ResearchStore(tmp_path / "service")
    p = payload("screen")
    directory = store.directory(p["id"])
    directory.mkdir(mode=0o700)
    state = {"id": p["id"], "request": p, "status": "running", "limits": {"seconds": 1}}
    save_state(directory / "request.json", state)
    lease = store.lease()
    captured_kw: dict = {}

    class MockProcess:
        pid = 123456

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def mock_popen(*a, **kw):
        captured_kw.update(kw)
        return MockProcess()

    monkeypatch.setattr("subprocess.Popen", mock_popen)
    monkeypatch.setattr(store, "result", lambda *a: {"coverage": {"status": "complete"}})
    store.execute(directory, state, lease)
    assert captured_kw.get("cwd") == str(REPO_ROOT)


def test_recent_ignores_malformed_directories_and_missing_files(tmp_path):
    store = ResearchStore(tmp_path / "service")
    # Valid run
    p = payload("screen")
    d = store.directory(p["id"])
    d.mkdir(mode=0o700)
    save_state(d / "request.json", {"id": p["id"], "request": p, "status": "running"})

    # Invalid directory name (not 32 hex)
    bad_dir = tmp_path / "service" / "not_a_hex_id"
    bad_dir.mkdir(mode=0o700)
    save_state(bad_dir / "request.json", {"id": "bad", "request": p, "status": "running"})

    # Symlink directory
    sym = tmp_path / "service" / uuid.uuid4().hex
    sym.symlink_to(d)

    runs = store.recent()
    assert len(runs) == 1
    assert runs[0]["id"] == p["id"]


def test_research_client_preserves_bounded_conflict_detail_and_redacts_500(monkeypatch):
    def handler_409(request):
        return httpx.Response(409, json={"detail": "已有研究执行中，请查看进度，不重复启动。"})

    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(handler_409)),
    )
    with pytest.raises(ResearchUnavailable) as err:
        ResearchClient("http://research", "x" * 32).submit(payload())
    assert "已有研究执行中" in str(err.value)

    def handler_500(request):
        return httpx.Response(500, text="internal server traceback with TUSHARE_TOKEN=secret")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(handler_500)),
    )
    with pytest.raises(ResearchUnavailable) as err_500:
        ResearchClient("http://research", "x" * 32).submit(payload())
    assert "secret" not in str(err_500.value)
    assert "暂不可确认" in str(err_500.value)


def test_configured_client_graceful_on_bad_env(monkeypatch):
    from a_stock_tracker.research_client import configured_client

    monkeypatch.setenv("RESEARCH_SERVICE_URL", "http://research")
    monkeypatch.setenv("RESEARCH_SERVICE_TOKEN", "short")
    assert configured_client() is None


def test_refresh_resets_pending_payload_and_generates_new_nonce(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)

    async def run():
        page = AppMockPage()
        await app.build_app()(page)
        entry = next(
            c
            for c in app_controls(page)
            if isinstance(c, ft.Button) and c.content == "开始选股与研究"
        )
        await entry.on_click(None)
        radio_group = next(c for c in app_controls(page) if isinstance(c, ft.RadioGroup))
        radio_group.value = "swing"
        radio_group.on_change(None)
        codes_input = next(c for c in app_controls(page) if isinstance(c, ft.TextField))
        codes_input.value = "600519"
        consent = next(c for c in app_controls(page) if isinstance(c, ft.Checkbox))
        consent.value = True
        consent.on_change(None)
        start = next(
            c
            for c in app_controls(page)
            if isinstance(c, ft.Button) and c.content == "确认启动研究"
        )
        assert not start.disabled
        codes_input.value = "000001"
        codes_input.on_change(None)
        assert consent.value is False
        assert start.disabled is True
        assert start.content == "确认启动研究"
        await page.on_close(None)

    asyncio.run(run())


def test_analysis_invalid_route_and_unreachable_service_handled_cleanly(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = ResearchClient("http://127.0.0.1:54321", "x" * 32)
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)

    async def run():
        page = AppMockPage()
        await app.build_app()(page)
        page.route = "/analysis"
        await page.on_route_change(SimpleNamespace(route="/analysis"))
        assert any(
            isinstance(c, ft.Text) and "研究服务暂时无法连接" in str(c.value)
            for c in app_controls(page)
        )

        page.route = "/analysis/not-32-hex"
        await page.on_route_change(SimpleNamespace(route="/analysis/not-32-hex"))
        assert any(
            isinstance(c, ft.Text) and "请求编号不正确" in str(c.value) for c in app_controls(page)
        )
        await page.on_close(None)

    asyncio.run(run())
