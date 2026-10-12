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


@pytest.mark.parametrize("mode", ["swing", "financial"])
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
    with pytest.raises(Exception):
        ResearchStore(root)
    linked = tmp_path / "linked"
    linked.symlink_to(root)
    with pytest.raises(Exception):
        ResearchStore(linked)
    with pytest.raises(ValueError):
        create_service(tmp_path / "new", "short")
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
    assert "result" not in read_state(directory / "request.json")
    assert store.status(p["id"])["result"] == {"coverage": {"status": "complete"}}


def test_dead_leader_descendant_is_stopped_before_next_run(tmp_path, monkeypatch):
    store = ResearchStore(tmp_path / "service")
    p = payload("screen")
    directory = store.directory(p["id"])
    directory.mkdir(mode=0o700)
    pid_path = directory / "child.pid"
    script = (
        "import subprocess,sys,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); sys.exit(1)"
    )
    monkeypatch.setattr(store, "command", lambda *_: [sys.executable, "-c", script, str(pid_path)])
    state = {"id": p["id"], "request": p, "status": "running", "limits": {"seconds": 5}}
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
    assert store.status(new["id"])["result"] == {"synthetic": True}
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
        return [] if operation == "recent" else {"status": "running"}

    monkeypatch.setattr(ResearchStore, operation, slow)
    service = create_service(tmp_path / "service", "x" * 32)

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


def test_real_cost_notice_consent_resets_when_mode_changes(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")
    client = DemoResearchClient()
    info = dict(client.info(), demo=False)
    monkeypatch.setattr(client, "info", lambda: info)
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)

    async def run():
        page = AppMockPage()
        page.route = "/analysis?mode=swing&code=600519.SH"
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
                isinstance(c, ft.Text) and "不保证提供方停止生成或计费" in (c.value or "")
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
    from a_stock_tracker import automation as automation
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
    from a_stock_tracker import automation as automation
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
    from a_stock_tracker import automation as automation

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
        page.route = "/analysis"
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
            assert new["request"]["quote"]["requests"] == 400
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
