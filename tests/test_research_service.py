"""Requests stay bounded, authenticated, idempotent and separate from Web credentials."""

import asyncio
import json
import sys
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import flet as ft
import httpx
import pytest
from fastapi.testclient import TestClient

from a_stock_tracker import app
from a_stock_tracker.automation import read_state, save_state
from a_stock_tracker.research import ScreenError
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
    api = create_service(tmp_path / "service", {"web": "x" * 32})
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
        rejection = client.post("/runs", json=bad)
        assert rejection.status_code == 409
        assert rejection.json()["code"] == "quote_changed"
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


@pytest.mark.parametrize("mode", ["financial"])
def test_model_cost_boundary_requires_new_consent_before_execution(tmp_path, monkeypatch, mode):
    store = ResearchStore(tmp_path / "service")
    request = payload(mode)
    boundary = request["quote"].pop("model_budget")
    assert boundary["cost_hard_limit"] is None
    assert boundary["output_token_hard_limit"] is None
    monkeypatch.setattr(store, "lease", lambda: pytest.fail("accepted stale consent"))
    with pytest.raises(ValueError, match="重新确认"):
        store.submit(request)
    assert not store.directory(request["id"]).exists()
    request["quote"]["model_budget"] = {**boundary, "cost_hard_limit": 0}
    with pytest.raises(ValueError, match="重新确认"):
        store.submit(request)


def test_service_rejects_old_private_workspaces_symlinks_and_bad_tokens(tmp_path):
    root = tmp_path / "old"
    root.mkdir(mode=0o700)
    (root / "history.sqlite").write_text("historical data")
    with pytest.raises(ScreenError):
        ResearchStore(root)
    linked = tmp_path / "linked"
    linked.symlink_to(root)
    with pytest.raises(ScreenError):
        ResearchStore(linked)
    with pytest.raises(ValueError):
        create_service(tmp_path / "new", {"web": "short"})
    assert (root / "history.sqlite").read_text() == "historical data"


@pytest.mark.parametrize("case", ["same", "inside", "above", "extra", "valid"])
def test_service_socket_bridge_is_separate_and_socket_only(tmp_path, monkeypatch, case):
    from a_stock_tracker import research_service

    root = tmp_path / "artifacts"
    bridge = {
        "same": root,
        "inside": root / "bridge",
        "above": tmp_path,
    }.get(case, tmp_path / "bridge")
    if case == "extra":
        bridge.mkdir(mode=0o700)
        (bridge / "private.txt").write_text("synthetic private data")
    monkeypatch.setenv("RESEARCH_SERVICE_ROOT", str(root))
    monkeypatch.setenv("RESEARCH_SERVICE_TOKEN", "x" * 32)
    monkeypatch.setenv("RESEARCH_SERVICE_SOCKET", str(bridge / "service.sock"))
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append(kw))
    if case == "valid":
        research_service.main()
        assert calls[0]["uds"] == str(bridge / "service.sock")
    else:
        with pytest.raises(ValueError, match="separate|socket-only"):
            research_service.main()
        assert not calls


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
    assert "--pb-mv-screen" in store.command(store.directory(p["id"]), payload("screen"))
    assert "--pb-mv-screen" not in store.command(store.directory(p["id"]), payload("financial"))


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
                if isinstance(c, ft.TextButton) and c.content == "从初筛找公司"
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


def test_research_entry_loads_independent_reads_concurrently(monkeypatch):
    import threading

    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)
    barrier = threading.Barrier(2)

    def concurrent(method):
        def read():
            barrier.wait(timeout=3)
            return method()

        return read

    monkeypatch.setattr(client, "info", concurrent(client.info))
    monkeypatch.setattr(client, "recent", concurrent(client.recent))

    async def run():
        page = AppMockPage()
        page.route = "/analysis"
        try:
            await app.build_app()(page)
            assert any(
                isinstance(c, ft.Button) and c.content == "确认启动研究" for c in app_controls(page)
            )
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_research_poll_only_renders_changed_data(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)
    identifier = "a" * 32
    initial = {
        "id": identifier,
        "status": "running",
        "message": "采集中",
        "created_at": "2026-09-22T10:00:00+08:00",
        "limits": {"title": "合成研究"},
    }
    snapshots = iter(
        [
            initial,
            dict(initial),
            dict(initial, message="解释中"),
            dict(initial, status="completed", message="完成"),
        ]
    )
    monkeypatch.setattr(client, "status", lambda _: next(snapshots))
    rendered = []
    original_view = app.result_view

    def result_view(run, *args, **kwargs):
        rendered.append(run)
        return original_view(run, *args, **kwargs)

    monkeypatch.setattr(app, "result_view", result_view)
    original_sleep = asyncio.sleep

    async def run():
        ready = asyncio.Event()
        ticks = asyncio.Queue()

        async def sleep(delay):
            if delay == 2:
                ready.set()
                await ticks.get()
            else:
                await original_sleep(delay)

        monkeypatch.setattr(app.asyncio, "sleep", sleep)
        page = AppMockPage()
        page.route = f"/analysis/{identifier}"
        try:
            await app.build_app()(page)
            await asyncio.wait_for(ready.wait(), 3)
            poll = next(t for t in asyncio.all_tasks() if t.get_coro().__name__ == "poll")
            initial_updates = page.updated_count
            for expected_renders in (1, 2):
                ready.clear()
                ticks.put_nowait(None)
                await asyncio.wait_for(ready.wait(), 3)
                assert len(rendered) == expected_renders
                assert page.updated_count == initial_updates + expected_renders - 1
            ticks.put_nowait(None)
            await asyncio.wait_for(poll, 3)
            assert len(rendered) == 3
            assert rendered[-1]["status"] == "completed"
            assert page.updated_count == initial_updates + 2
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
            if isinstance(c, ft.TextButton) and c.content == "从初筛找公司"
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
                isinstance(c, ft.Text) and c.value == "选择公司研究财报" for c in app_controls(page)
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
    assert "合成证据缺口" in result["reports"][0]["error"]
    assert "unverified claims" not in result["reports"][0]["error"]
    (run / "report-600519.SH.json").write_text("{}")
    with pytest.raises(ScreenError):
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
    assert "result" not in read_state(directory / "request.json")
    assert store.status(p["id"])["result"] == {"coverage": {"status": "complete"}}


@pytest.mark.parametrize("stop_reason", ["leader_exit", "timeout"])
def test_descendant_is_stopped_before_next_run(tmp_path, monkeypatch, stop_reason):
    store = ResearchStore(tmp_path / "service")
    p = payload("screen")
    directory = store.directory(p["id"])
    directory.mkdir(mode=0o700)
    pid_path = directory / "child.pid"
    script = (
        "import subprocess,sys,pathlib,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); "
        + ("sys.exit(1)" if stop_reason == "leader_exit" else "time.sleep(30)")
    )
    monkeypatch.setattr(store, "command", lambda *_: [sys.executable, "-c", script, str(pid_path)])
    state = {
        "id": p["id"],
        "request": p,
        "status": "running",
        "limits": {"seconds": 5 if stop_reason == "leader_exit" else -29},
    }
    store.execute(directory, state, store.lease())
    child = int(pid_path.read_text())
    for _ in range(100):
        stat = Path(f"/proc/{child}/stat")
        try:
            if stat.read_text().split()[2] == "Z":
                break
        except (FileNotFoundError, ProcessLookupError):
            break
        time.sleep(0.01)
    else:
        pytest.fail("descendant survived completed execution")
    assert state["status"] == "failed"
    store.lease().close()


def test_same_day_screen_reuses_verified_original_without_spending(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    store = ResearchStore(tmp_path / "service")
    original = payload("screen")
    directory = store.directory(original["id"])
    directory.mkdir(mode=0o700)
    old = {
        "id": original["id"],
        "request": original,
        "status": "partial",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "limits": original["quote"],
    }
    save_state(directory / "request.json", old)
    reads = []
    monkeypatch.setattr(store, "result", lambda path, _: reads.append(path) or {"synthetic": True})
    monkeypatch.setattr(
        "a_stock_tracker.research_service.Thread", lambda **_: pytest.fail("new spend")
    )
    new = payload("screen")
    accepted = store.submit(new)
    assert accepted["reused_from"] == old["id"]
    assert store.submit(new) == accepted
    result = store.status(new["id"])["result"]
    assert result["synthetic"] is True
    assert result["usage"]["requests"] == 0
    assert reads == [directory, directory]
    assert not (store.directory(new["id"]) / "batch").exists()
    store.lease().close()


def test_yesterday_screen_not_reused_and_disk_reserve_required(tmp_path, monkeypatch):
    from a_stock_tracker import research_service as service

    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    store = ResearchStore(tmp_path / "service")
    original = payload("screen")
    directory = store.directory(original["id"])
    directory.mkdir(mode=0o700)
    save_state(
        directory / "request.json",
        {
            "id": original["id"],
            "request": original,
            "status": "partial",
            "created_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        },
    )
    monkeypatch.setattr(service, "disk_bytes", lambda _: service.MAX_ROOT_BYTES)
    monkeypatch.setattr(store, "result", lambda *_: pytest.fail("stale reuse"))
    with pytest.raises(ValueError, match="存储预算"):
        store.submit(payload("screen"))
    store.lease().close()


@pytest.mark.parametrize("operation", ["status", "recent", "submit"])
def test_slow_store_does_not_block_service_event_loop(tmp_path, monkeypatch, operation):
    import threading

    started, release = threading.Event(), threading.Event()

    def slow(*args):
        started.set()
        assert release.wait(5)
        return [] if operation == "recent" else {"id": "a" * 32, "status": "running"}

    monkeypatch.setattr(ResearchStore, operation, slow)
    service = create_service(tmp_path / "service", {"web": "x" * 32})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=service),
            base_url="http://research",
            headers={"Authorization": "Bearer " + "x" * 32},
        ) as client:
            path = "/runs/" + "a" * 32 if operation == "status" else "/runs"
            method = "POST" if operation == "submit" else "GET"
            pending = asyncio.create_task(client.request(method, path, json=payload()))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                response = await asyncio.wait_for(client.get("/info"), 2)
                assert response.status_code == 200
                assert not pending.done()
            finally:
                release.set()
                assert (await pending).status_code == 200

    asyncio.run(run())


def test_result_reverified_outside_request_lock_and_not_in_history(tmp_path, monkeypatch):
    from a_stock_tracker.automation import locked_root

    store = ResearchStore(tmp_path / "service")
    p = payload()
    directory = store.directory(p["id"])
    directory.mkdir(mode=0o700)
    save_state(
        directory / "request.json",
        {"id": p["id"], "request": p, "status": "completed", "result": {"old": True}},
    )
    verified = []

    def result(*args):
        with locked_root(store.root):
            verified.append(True)
        return {"verified": len(verified)}

    monkeypatch.setattr(store, "result", result)
    assert "result" not in store.status(p["id"], include_result=False)
    assert not verified
    assert store.status(p["id"])["result"] == {"verified": 1}
    assert store.status(p["id"])["result"] == {"verified": 2}
    assert read_state(directory / "request.json")["result"] == {"old": True}


def test_recent_selects_only_ten_newest_states(tmp_path, monkeypatch):
    import os

    store = ResearchStore(tmp_path / "service")
    for number in range(30):
        directory = store.directory(f"{number:032x}")
        directory.mkdir(mode=0o700)
        path = directory / "request.json"
        save_state(path, {"id": directory.name, "status": "failed"})
        os.utime(path, (number + 1, number + 1))
    calls = []
    original = store.status

    def status(identifier, **kwargs):
        calls.append(identifier)
        return original(identifier, **kwargs)

    monkeypatch.setattr(store, "status", status)
    runs = store.recent()
    assert [r["id"] for r in runs] == [f"{n:032x}" for n in range(29, 19, -1)]
    assert len(calls) == 10


def test_recent_handles_concurrent_file_deletion_during_stat(tmp_path, monkeypatch):
    import errno
    from pathlib import Path

    store = ResearchStore(tmp_path / "service")
    p1 = payload("screen")
    d1 = store.directory(p1["id"])
    d1.mkdir(mode=0o700)
    save_state(d1 / "request.json", {"id": p1["id"], "request": p1, "status": "running"})

    p2 = payload("swing")
    d2 = store.directory(p2["id"])
    d2.mkdir(mode=0o700)
    path2 = d2 / "request.json"
    save_state(path2, {"id": p2["id"], "request": p2, "status": "running"})

    original_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self == path2 and kwargs.get("follow_symlinks", True):
            raise FileNotFoundError(errno.ENOENT, "concurrent deletion")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    runs = store.recent()
    assert len(runs) == 1
    assert runs[0]["id"] == p1["id"]


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
            if isinstance(c, ft.TextButton) and c.content == "从初筛找公司"
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


def test_real_cost_notice_consent_resets_when_mode_changes(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    info = {**client.info().model_dump(by_alias=True, exclude_unset=True), "demo": False}
    monkeypatch.setattr(client, "info", lambda: info)
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)

    async def run():
        page = AppMockPage()
        page.route = "/analysis?mode=financial&code=600519.SH"
        try:
            await app.build_app()(page)
            controls = list(app_controls(page))
            consent = next(c for c in controls if isinstance(c, ft.Checkbox))
            start = next(
                c for c in controls if isinstance(c, ft.Button) and c.content == "确认启动研究"
            )
            mode = next(c for c in controls if isinstance(c, ft.RadioGroup))
            assert "接受模型费用无硬上限" in consent.label
            assert any(
                isinstance(c, ft.Text) and "不保证提供方停止计费" in (c.value or "")
                for c in controls
            )
            assert start.disabled and not consent.value
            consent.value = True
            consent.on_change(None)
            assert not start.disabled
            mode.value = "screen"
            mode.on_change(None)
            assert start.disabled and not consent.value
            assert "费用无硬上限" not in consent.label
            mode.value = "financial"
            mode.on_change(None)
            assert start.disabled and not consent.value
            assert "接受模型费用无硬上限" in consent.label
            assert not client.runs
        finally:
            await page.on_close(None)

    asyncio.run(run())


def screen_checkpoint_fixture(tmp_path):
    from a_stock_tracker import automation
    from tests.test_automation import basic, fixture, records

    store = ResearchStore(tmp_path / "service")
    request = payload("screen")
    directory = store.directory(request["id"])
    run = directory / "batch" / "run-2026-10-12T14-44-08-a321278e88e6"
    run.mkdir(mode=0o700, parents=True)
    frozen, state = fixture(5)
    frozen["prefilter"] = dict(automation.PB_MV_SCREEN)
    for row in frozen["rows"]:
        row["total_mv"] = 600000
    valuation = {"pb": 4, "total_mv": 600000}
    frozen["rows"].append(
        automation.prefilter_row(
            automation.initial_row(basic(6), valuation, frozen["data_date"]), valuation
        )
    )
    frozen["cutoff"] = datetime.now().date().isoformat()
    state.update(
        research={},
        model_calls=0,
        model={"provider": "p", "model": "m"},
        queue=[],
        cursor=0,
        last_industry="",
        notification="not_enabled",
    )
    state["freeze"] = automation.save(run / "universe.json", frozen)
    row = frozen["rows"][0]
    name = f"scan-{row['code']}.json"
    save_state(
        run / name,
        {
            "freeze": state["freeze"],
            "result": automation.evaluate(row, records(row["code"]), frozen),
        },
    )
    state["scan"][row["code"]] = automation.save(run / name, automation.read_result(run / name))
    save_state(run / "state.json", state)
    save_state(
        directory / "request.json",
        {
            "id": request["id"],
            "request": request,
            "status": "partial",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "limits": request["quote"],
            "message": "synthetic",
        },
    )
    return store, request, run


def test_continuation_copies_verified_checkpoint_and_preserves_original(tmp_path, monkeypatch):
    from a_stock_tracker import automation
    from tests.test_automation import Client

    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    store, original, source = screen_checkpoint_fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    leases = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            leases.append(args[2])

        def start(self):
            pass

    monkeypatch.setattr("a_stock_tracker.research_service.Thread", DeferredThread)
    request = {**payload("screen"), "resume_from": original["id"]}
    request["quote"] = quote(request_info(), "screen", 4)
    accepted = store.submit(request)
    assert accepted["status"] == "running" and accepted["continued_from"] == original["id"]
    assert store.submit(request) == accepted and len(leases) == 1
    leases.pop().close()
    with pytest.raises(ValueError, match="已发起后续"):
        store.submit({**request, "id": uuid.uuid4().hex})
    destination = store.directory(request["id"])
    command = store.command(destination, request)
    assert command[command.index("--resume") + 1] == source.name
    assert command[command.index("--max-research") + 1] == "0"
    assert "--pb-mv-screen" in command
    assert command[command.index("--screen-contract") + 1] == automation.digest(
        automation.PB_MV_SCREEN
    )
    copied = destination / "batch" / source.name
    state = read_state(copied / "state.json")
    frozen = automation.read_result(copied / "universe.json", state["freeze"])
    client = Client()
    automation.scan(copied, frozen, state, client, "synthetic", automation.Budget(50, 300))
    assert len(client.calls) == 4 and frozen["rows"][0]["code"] not in client.calls
    assert frozen["rows"][-1]["status"] == "excluded"
    assert frozen["rows"][-1]["code"] not in client.calls
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}
    assert store.result(destination, "screen")["coverage"]["counts"]["qualified"] == 5


@pytest.mark.parametrize("damage", ["hash", "symlink", "expired", "complete", "model_calls"])
def test_continuation_refuses_invalid_checkpoints_before_spend(tmp_path, monkeypatch, damage):
    from a_stock_tracker import automation

    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    store, original, source = screen_checkpoint_fixture(tmp_path)
    if damage == "hash":
        next(source.glob("scan-*")).write_text("{}")
    elif damage == "symlink":
        target = next(source.glob("scan-*"))
        moved = tmp_path / "moved.json"
        target.rename(moved)
        target.symlink_to(moved)
    elif damage == "expired":
        frozen = automation.read_result(source / "universe.json")
        frozen["cutoff"] = "2020-01-01"
        state = read_state(source / "state.json")
        state["freeze"] = automation.save(source / "universe.json", frozen)
        save_state(source / "state.json", state)
    elif damage == "complete":
        path = source.parent.parent / "request.json"
        state = read_state(path)
        state["status"] = "completed"
        save_state(path, state)
    else:
        state = read_state(source / "state.json")
        state["model_calls"] = 1
        save_state(source / "state.json", state)
    monkeypatch.setattr(
        "a_stock_tracker.research_service.Thread", lambda **_: pytest.fail("unexpected spend")
    )
    request = {**payload("screen"), "resume_from": original["id"]}
    with pytest.raises((ValueError, automation.ScreenError)):
        store.submit(request)
    assert not store.directory(request["id"]).exists()
    store.lease().close()


def test_entry_continuation_requires_explicit_new_budget_confirmation(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    original = payload("screen")
    original["quote"] = quote(client.info(), "screen")
    client.submit(original)
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)

    async def run():
        page = AppMockPage()
        page.route = "/analysis?mode=screen"
        try:
            await app.build_app()(page)
            controls = list(app_controls(page))
            start = next(
                c
                for c in controls
                if isinstance(c, ft.Button) and c.content == "确认新增预算并继续初筛"
            )
            assert start.disabled and len(client.runs) == 1
            await start.on_click(None)
            assert len(client.runs) == 1
            consent = next(c for c in controls if isinstance(c, ft.Checkbox))
            consent.value = True
            consent.on_change(None)
            await start.on_click(None)
            assert len(client.runs) == 2
            new = list(client.runs.values())[-1]
            assert new["request"]["resume_from"] == original["id"]
            assert new["request"]["quote"]["requests"] == 21
            assert "model_budget" not in new["request"]["quote"]
        finally:
            await page.on_close(None)

    asyncio.run(run())


@pytest.mark.parametrize("change", ["missing", "threshold"])
def test_screen_rule_requires_new_consent(tmp_path, monkeypatch, change):
    store = ResearchStore(tmp_path / "service")
    request = deepcopy(payload("screen"))
    if change == "missing":
        request["quote"].pop("prefilter")
    else:
        request["quote"]["prefilter"]["pb_max"] = 2
    monkeypatch.setattr(store, "lease", lambda: pytest.fail("accepted stale scope"))
    with pytest.raises(ValueError, match="重新确认"):
        store.submit(request)
    assert not store.directory(request["id"]).exists()


def test_legacy_screen_remains_readable_but_cannot_resume_under_new_rule(tmp_path, monkeypatch):
    from a_stock_tracker import automation

    store, original, source = screen_checkpoint_fixture(tmp_path)
    state = read_state(source / "state.json")
    frozen = automation.read_result(source / "universe.json", state["freeze"])
    frozen.pop("prefilter")
    state["freeze"] = automation.save(source / "universe.json", frozen)
    save_state(source / "state.json", state)
    assert store.status(original["id"])["can_continue"] is False
    assert store.result(source.parent.parent, "screen")["coverage"]["prefilter"] is None
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    request = {**payload("screen"), "resume_from": original["id"]}
    with pytest.raises(ValueError, match="规则已变化"):
        store.submit(request)
    assert not store.directory(request["id"]).exists()


def test_screen_quote_uses_receiver_scope_not_web_default():
    info = deepcopy(request_info())
    info["modes"]["screen"]["prefilter"]["pb_max"] = 2
    assert quote(info, "screen")["prefilter"]["pb_max"] == 2


@pytest.mark.parametrize("drift", ["pb_max", "total_mv_min_yi", "rule"])
def test_child_refuses_changed_confirmed_screen_before_market_access(tmp_path, monkeypatch, drift):
    import tushare

    from a_stock_tracker import automation

    request = payload("screen")
    command = ResearchStore(tmp_path / "store").command(tmp_path / "batch", request)
    assert command[command.index("--screen-contract") + 1] == automation.digest(
        request["quote"]["prefilter"]
    )
    changed = {**automation.PB_MV_SCREEN, drift: "changed" if drift == "rule" else 2}
    monkeypatch.setattr(automation, "PB_MV_SCREEN", changed)
    monkeypatch.setattr(sys, "argv", ["automation", *command[3:]])
    monkeypatch.setattr(
        tushare, "pro_api", lambda *_, **__: pytest.fail("accessed market before confirming scope")
    )
    with pytest.raises(SystemExit) as error:
        automation.main()
    assert error.value.code == 2
    assert not (tmp_path / "batch").exists()


def test_resume_quote_scales_with_remaining_and_pins_child_limits(tmp_path, monkeypatch):
    store, original, _ = screen_checkpoint_fixture(tmp_path)
    status = store.status(original["id"])
    offered = status["continuation_quote"]
    assert offered["requests"] == 24 and offered["seconds"] == 66
    assert quote(request_info(), "screen")["requests"] == 1500
    assert quote(request_info(), "screen")["seconds"] == 3600
    assert quote(request_info(), "screen", 2000)["seconds"] == 3600
    assert quote(request_info(), "screen", 100)["seconds"] == 288
    request = {**payload("screen"), "quote": offered, "resume_from": original["id"]}
    destination = store.directory(request["id"])
    destination.mkdir(mode=0o700)
    command = store.command(destination, request)
    assert command[command.index("--request-budget") + 1] == "24"
    assert command[command.index("--seconds") + 1] == "66"


def test_reuse_follows_chain_tail_and_recent_groups_all_references(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    store, original, source = screen_checkpoint_fixture(tmp_path)
    first = read_state(source.parent.parent / "request.json")
    tail_id = uuid.uuid4().hex
    tail = store.directory(tail_id)
    tail.mkdir(mode=0o700)
    save_state(
        tail / "request.json",
        {
            **first,
            "id": tail_id,
            "continued_from": original["id"],
            "request": {**payload("screen"), "quote": quote(request_info(), "screen", 4)},
        },
    )
    reads = []
    monkeypatch.setattr(
        store,
        "result",
        lambda path, mode: (
            reads.append(path.name)
            or {
                "coverage": {
                    "counts": {"pending": 2},
                    "cutoff": datetime.now().date().isoformat(),
                    "prefilter": first["limits"]["prefilter"],
                }
            }
        ),
    )
    for _ in range(12):
        request = payload("screen")
        accepted = store.submit(request)
        assert accepted["reused_from"] == tail_id
        assert store.submit(request)["id"] == request["id"]
    assert set(reads) == {tail_id}
    old = store.status(original["id"])
    assert old["continued_by"] == tail_id and old["can_continue"] is False
    recent = store.recent()
    assert len(recent) == 1 and recent[0]["id"] == tail_id
    assert recent[0]["continuations"] == 1 and recent[0]["reused"]


@pytest.mark.parametrize("source", ["../x", "a" * 31, "A" * 32, 123, "f" * 32])
def test_source_metadata_rejects_invalid_or_foreign_reference(tmp_path, monkeypatch, source):
    store = ResearchStore(tmp_path / "service")
    request = {**payload(), "from": source}
    monkeypatch.setattr(store, "lease", lambda: pytest.fail("invalid source spent budget"))
    with pytest.raises((ValueError, FileNotFoundError)):
        store.submit(request)
    assert not store.directory(request["id"]).exists()


def test_source_metadata_accepts_only_local_screen_and_rejects_symlink(tmp_path, monkeypatch):
    store, original, source = screen_checkpoint_fixture(tmp_path)
    info = request_info()
    for mode in info["modes"].values():
        mode["available"] = True
    monkeypatch.setattr("a_stock_tracker.research_service.request_info", lambda: info)
    leases = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            leases.append(args[2])

        def start(self):
            pass

    monkeypatch.setattr("a_stock_tracker.research_service.Thread", DeferredThread)
    request = {**payload(), "from": original["id"]}
    try:
        accepted = store.submit(request)
        assert accepted["request"]["from"] == original["id"]
    finally:
        for lease in leases:
            lease.close()
    with pytest.raises(ValueError, match="来源必须"):
        store.submit({**payload(), "from": request["id"]})
    linked = store.directory(uuid.uuid4().hex)
    linked.symlink_to(source.parent.parent)
    with pytest.raises(ScreenError):
        store.submit({**payload(), "from": linked.name})


def test_complete_screen_groups_are_not_batch_queue_and_response_is_bounded(tmp_path, monkeypatch):
    from a_stock_tracker import automation
    from tests.test_automation import Client

    store, original, source = screen_checkpoint_fixture(tmp_path)
    state = read_state(source / "state.json")
    frozen = automation.read_result(source / "universe.json", state["freeze"])
    automation.scan(source, frozen, state, Client(), "synthetic", automation.Budget(50, 300))
    base = store.result(source.parent.parent, "screen")
    assert len(base["groups"][0]["ranking"]) == 5
    row = base["groups"][0]["ranking"][0]
    assert set(row) == {
        "code",
        "name",
        "pb",
        "roe_mean",
        "total_mv",
        "order",
        "pb_rank",
        "roe_rank",
    }
    assert not base["truncated"] and base["usage"] is None
    real_coverage = automation.coverage

    def expanded(rows, frozen):
        summary = real_coverage(rows, frozen)
        summary["groups"][0]["ranking"] *= 6000
        return summary

    monkeypatch.setattr("a_stock_tracker.research_service.coverage", expanded)
    result = store.result(source.parent.parent, "screen")
    assert result["truncated"] and "上限" in result["truncation_note"]
    assert 5 < len(result["groups"][0]["ranking"]) < 30000
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 3_900_000


@pytest.mark.parametrize(
    "response_code,error_type",
    [("quote_changed", "ResearchQuoteChanged"), ("busy", "ResearchRejected")],
)
def test_client_distinguishes_explicit_rejection(monkeypatch, response_code, error_type):
    from a_stock_tracker import research_client

    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(409, json={"code": response_code, "detail": "请重新确认"})
            )
        ),
    )
    with pytest.raises(getattr(research_client, error_type)):
        ResearchClient("http://research", "x" * 32).submit(payload())


@pytest.mark.parametrize("refresh_fails", [False, True])
def test_quote_rejection_preserves_input_and_requires_fresh_consent(monkeypatch, refresh_fails):
    from a_stock_tracker.research_client import ResearchQuoteChanged

    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)
    submitted = []
    original_submit, original_info = client.submit, client.info

    def reject_once(request):
        submitted.append(deepcopy(request))
        if len(submitted) == 1:
            raise ResearchQuoteChanged("报价已变化")
        return original_submit(request)

    monkeypatch.setattr(client, "submit", reject_once)

    async def run():
        page = AppMockPage()
        page.route = "/analysis?mode=swing&code=600519"
        try:
            await app.build_app()(page)
            field = next(c for c in app_controls(page) if isinstance(c, ft.TextField))
            consent = next(c for c in app_controls(page) if isinstance(c, ft.Checkbox))
            start = next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.Button) and c.content == "确认启动研究"
            )
            if refresh_fails:
                monkeypatch.setattr(
                    client, "info", lambda: (_ for _ in ()).throw(ResearchUnavailable("离线"))
                )
            consent.value = True
            consent.on_change(None)
            await start.on_click(None)
            assert field.value == "600519" and not consent.value and not client.runs
            if refresh_fails:
                assert start.content == "重新获取报价"
                monkeypatch.setattr(client, "info", original_info)
                await start.on_click(None)
            assert start.disabled and start.content == "确认启动研究"
            await start.on_click(None)
            assert len(submitted) == 1
            consent.value = True
            consent.on_change(None)
            await start.on_click(None)
            assert len(client.runs) == 1 and len(submitted) == 2
            assert submitted[0]["id"] != submitted[1]["id"]
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_late_home_research_response_cannot_replace_new_page(monkeypatch):
    from threading import Event
    from types import SimpleNamespace

    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    started, release = Event(), Event()

    def delayed_recent():
        started.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(client, "recent", delayed_recent)
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)

    async def run():
        page = AppMockPage()
        pending = asyncio.create_task(app.build_app()(page))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            page.route = "/settings"
            await page.on_route_change(SimpleNamespace(route="/settings"))
        finally:
            release.set()
            await pending
        try:
            text = "\n".join(str(getattr(c, "value", "")) for c in app_controls(page))
            assert "账户与运行信息" in text and "研究首页" not in text
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_price_quote_has_no_model_and_rejects_old_model_consent(tmp_path, monkeypatch):
    from a_stock_tracker.research_service import QuoteChanged

    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    monkeypatch.delenv("RESEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("RESEARCH_MODEL", raising=False)
    info = request_info()
    assert info["modes"]["swing"]["available"]
    assert not info["modes"]["financial"]["available"]
    request = payload()
    assert request["quote"]["model"] is None and "model_budget" not in request["quote"]
    store = ResearchStore(tmp_path / "root")
    command = store.command(store.directory(request["id"]), request)
    assert "--model" not in command and "--provider" not in command
    request["quote"]["model_budget"] = {"calls": 10}
    monkeypatch.setattr(store, "lease", lambda: pytest.fail("accepted old quote"))
    with pytest.raises(QuoteChanged):
        store.submit(request)


def test_structured_projection_joins_sources_and_never_returns_rejected_draft():
    from a_stock_tracker.disclosures import structured_report

    result = {
        "code": "600001.SH",
        "status": "incomplete",
        "evidence_review": "passed",
        "evidence": {
            "structured": {
                "industry": "合成行业",
                "valuation_date": "2026-04-01",
                "pb": 1.2,
                "roe_mean": 12.0,
            },
            "documents": [
                {
                    "id": "D1",
                    "title": "合成年报",
                    "period": "2025-12-31",
                    "announced": "2026-04-01",
                    "url": "https://example.invalid/a.pdf",
                    "pages": [{"page": 12, "text": "私人全文"}],
                }
            ],
            "scope_gaps": ["未核查新闻"],
            "omitted_pages": [{"page": 13}],
        },
        "report": {
            "analysis": {"business": {"text": "推论", "facts": ["F1"]}},
            "facts": [
                {"id": "F1", "claim": "合成事实", "document": "D1", "page": 12, "quote": "合成引文"}
            ],
            "gaps": ["回款缺口"],
        },
    }
    before = deepcopy(result)
    report = structured_report(result)
    assert report["facts"][0] == {
        "id": "F1",
        "claim": "合成事实",
        "page": 12,
        "quote": "合成引文",
        "title": "合成年报",
        "period": "2025-12-31",
        "announced": "2026-04-01",
        "url": "https://example.invalid/a.pdf",
    }
    assert report["documents"] == [{"period": "2025-12-31", "announced": "2026-04-01"}]
    assert report["gaps"] == ["未核查新闻", "回款缺口"] and report["omitted_pages"] == 1
    assert report["analysis"]["business"]["facts"] == ["F1"]
    assert "私人全文" not in json.dumps(report, ensure_ascii=False)
    assert result == before
    result["evidence_review"] = "failed"
    result["rejected_draft"] = result["report"]
    report = structured_report(result)
    assert report["analysis"] == {} and report["facts"] == []
    assert "rejected_draft" not in report


def test_recent_revalidates_price_expiry_without_persisting_summaries(tmp_path, monkeypatch):
    from a_stock_tracker import opportunities
    from tests.test_opportunities import saved_board

    store = ResearchStore(tmp_path / "root")
    request = payload()
    directory = store.directory(request["id"])
    batch = directory / "batch"
    batch.mkdir(parents=True, mode=0o700)
    board = saved_board(batch)
    expiry = datetime.fromisoformat(board["rows"][0]["expires"]).date()
    state = {"id": request["id"], "request": request, "status": "completed"}
    save_state(directory / "request.json", state)
    monkeypatch.setattr(opportunities, "today", lambda: expiry)
    assert not store.recent()[0]["summary"]["board"]["rows"][0]["expired"]
    monkeypatch.setattr(opportunities, "today", lambda: expiry + timedelta(days=1))
    assert store.recent()[0]["summary"]["board"]["rows"][0]["expired"]
    assert read_state(directory / "request.json") == state
    output = batch / ("swing-" + "a" * 32) / "result.json"
    output.write_text("{}")
    assert store.recent() == []


def test_receiver_and_batch_death_keep_model_lease_until_child_exits(tmp_path):
    """Real subprocesses, no model/provider calls; also exercise restarted status reads."""
    import os
    import signal
    import subprocess

    from a_stock_tracker.research_service import REPO_ROOT

    root = tmp_path / "service"
    leader_path = tmp_path / "leader.pid"
    child_path = tmp_path / "child.pid"
    stop_path = tmp_path / "stop"
    child_script = """
import os, pathlib, sys, time
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
deadline = time.monotonic() + 15
while not pathlib.Path(sys.argv[2]).exists() and time.monotonic() < deadline:
    time.sleep(.01)
"""
    leader_script = """
import os, pathlib, sys
from a_stock_tracker.bounded_process import run_bounded
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
run_bounded([sys.executable, '-c', sys.argv[2], sys.argv[3], sys.argv[4]], timeout=20)
"""
    receiver_script = """
import pathlib, sys
from a_stock_tracker.automation import save_state
from a_stock_tracker.research_service import ResearchStore
store = ResearchStore(pathlib.Path(sys.argv[1]))
directory = store.directory('a' * 32)
directory.mkdir(mode=0o700)
state = {'id': 'a' * 32, 'request': {'mode': 'swing'}, 'status': 'running', 'limits': {'seconds': 20}}
save_state(directory / 'request.json', state)
store.command = lambda *_: [sys.executable, '-c', sys.argv[2], *sys.argv[3:]]
store.execute(directory, state, store.lease())
"""
    receiver = subprocess.Popen(
        [
            sys.executable,
            "-c",
            receiver_script,
            str(root),
            leader_script,
            str(leader_path),
            child_script,
            str(child_path),
            str(stop_path),
        ],
        cwd=REPO_ROOT,
    )
    try:
        deadline = time.monotonic() + 10
        # write_text creates the file before writing; wait for the pid itself.
        while not (child_path.exists() and child_path.read_text().strip()):
            assert receiver.poll() is None, "synthetic receiver exited before child started"
            assert time.monotonic() < deadline, "synthetic child failed to start"
            time.sleep(0.01)
        child = int(child_path.read_text())
        leader = int(leader_path.read_text())
        receiver.kill()
        receiver.wait(timeout=3)
        restarted = ResearchStore(root)
        with pytest.raises(BlockingIOError):
            restarted.lease()
        os.kill(leader, signal.SIGKILL)
        # Observe the lease after the leader is truly dead, not while its FD is still open.
        deadline = time.monotonic() + 3
        while True:
            try:
                if Path(f"/proc/{leader}/stat").read_text().split()[2] == "Z":
                    break
            except (FileNotFoundError, ProcessLookupError):
                break
            assert time.monotonic() < deadline, "synthetic leader did not exit"
            time.sleep(0.01)
        assert Path(f"/proc/{child}/stat").read_text().split()[2] != "Z"
        with pytest.raises(BlockingIOError):
            restarted.lease()
        assert restarted.status("a" * 32)["status"] == "running"
        stop_path.touch()
        deadline = time.monotonic() + 3
        while True:
            try:
                restarted.lease().close()
                break
            except BlockingIOError:
                assert time.monotonic() < deadline, "exited child retained execution lease"
                time.sleep(0.01)
        assert restarted.status("a" * 32)["status"] == "interrupted"
    finally:
        if receiver.poll() is None:
            receiver.kill()
        receiver.wait(timeout=3)
        if leader_path.exists():
            try:
                os.killpg(int(leader_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
