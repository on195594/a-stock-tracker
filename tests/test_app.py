"""Application authentication, lifecycle and ASGI security regressions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import flet as ft
import pytest

from a_stock_tracker import app
from a_stock_tracker.app import SecurityMiddleware
from a_stock_tracker.auth import AuthError, check_production_auth_config


@pytest.mark.parametrize(
    "path,host,status",
    [
        ("/upload/malicious_file", b"127.0.0.1:8550", 404),
        ("/", b"external.domain.com", 403),
        ("/", b"127.0.0.1:8550", 200),
        ("/research", b"127.0.0.1:8550", 404),
        ("/discover", b"127.0.0.1:8550", 404),
        ("/company/600001.SH", b"127.0.0.1:8550", 404),
        ("/jobs/old", b"127.0.0.1:8550", 404),
    ],
)
def test_asgi_demo_request_boundary(path, host, status):
    async def _test():
        called = False

        async def dummy_app(scope, receive, send):
            nonlocal called
            called = True
            await send({"type": "http.response.start", "status": 200})

        middleware = SecurityMiddleware(
            dummy_app, is_demo=True, public_base_url="http://127.0.0.1:8550"
        )

        scope = {
            "type": "http",
            "path": path,
            "headers": [(b"host", host)],
        }
        sent = []

        async def mock_receive():
            return {"type": "http.request"}

        async def mock_send(msg):
            sent.append(msg)

        await middleware(scope, mock_receive, mock_send)
        assert called is (status == 200)
        assert sent[0]["type"] == "http.response.start"
        assert sent[0]["status"] == status

    asyncio.run(_test())


def test_asgi_strict_host_and_origin_probes():
    async def dummy_app(scope, receive, send):
        pass

    s = SecurityMiddleware(dummy_app, is_demo=False, public_base_url="https://stocks.example.com")

    # Host validation tests
    assert s._validate_host("stocks.example.com") is True
    assert s._validate_host("stocks.example.com:443") is True
    assert s._validate_host("stocks.example.com:8080") is False
    assert s._validate_host("stocks.example.com:bad") is False
    assert s._validate_host("stocks.example.com:443:extra") is False
    assert s._validate_host("stocks.example.com:65536") is False
    assert s._validate_host("evil.example.com") is False

    # Origin validation tests
    assert s._validate_origin("https://stocks.example.com") is True
    assert s._validate_origin("https://stocks.example.com:443") is True
    assert s._validate_origin("https://stocks.example.com/") is False
    assert s._validate_origin("https://stocks.example.com/other") is False
    assert s._validate_origin("https://stocks.example.com?x=1") is False
    assert s._validate_origin("https://stocks.example.com#fragment") is False
    assert s._validate_origin("https://user:pass@stocks.example.com") is False
    assert s._validate_origin("https://stocks.example.com:8080") is False
    assert s._validate_origin("https://stocks.example.com:bad") is False
    assert s._validate_origin("https://stocks.example.com:65536") is False


def test_asgi_demo_origin_and_port_checks():
    async def dummy_app(scope, receive, send):
        pass

    s = SecurityMiddleware(dummy_app, is_demo=True, public_base_url="http://127.0.0.1:8550")

    # Correct origin matches configured base URL strictly under RFC 6454
    assert s._validate_origin("http://127.0.0.1:8550") is True
    assert s._validate_origin("http://localhost:8550") is False

    # Different port rejected in demo
    assert s._validate_origin("http://127.0.0.1:9999") is False

    # Invalid port strings and out-of-range ports rejected without exception
    assert s._validate_origin("http://127.0.0.1:bad") is False
    assert s._validate_origin("http://127.0.0.1:65536") is False
    assert s._validate_host("127.0.0.1:bad") is False
    assert s._validate_host("127.0.0.1:65536") is False
    assert s._validate_host("127.0.0.1:8550") is True


@pytest.mark.parametrize(
    "origin,allowed",
    [
        (None, False),
        (b"http://127.0.0.1.evil.com:8550", False),
        (b"http://127.0.0.1:8550", True),
    ],
)
def test_asgi_websocket_origin_check(origin, allowed):
    async def _test():
        ws_called = False
        sent = []

        async def dummy_app(scope, receive, send):
            nonlocal ws_called
            ws_called = True

        async def mock_send(msg):
            sent.append(msg)

        middleware = SecurityMiddleware(
            dummy_app, is_demo=True, public_base_url="http://127.0.0.1:8550"
        )
        headers = [(b"host", b"127.0.0.1:8550")]
        if origin is not None:
            headers.append((b"origin", origin))
        await middleware({"type": "websocket", "path": "/ws", "headers": headers}, None, mock_send)
        assert ws_called is allowed
        if allowed:
            assert sent == []
        else:
            assert sent[0]["type"] == "websocket.close"

    asyncio.run(_test())


def test_production_auth_checks():
    # Missing client id
    with pytest.raises(AuthError, match="GITHUB_CLIENT_ID"):
        check_production_auth_config("", "sec", "123")

    # Missing client secret
    with pytest.raises(AuthError, match="GITHUB_CLIENT_SECRET"):
        check_production_auth_config("client", "", "123")

    # Missing allowed user ID
    with pytest.raises(AuthError, match="GITHUB_ALLOWED_USER_ID"):
        check_production_auth_config("client", "sec", "")

    # Non-numeric user ID
    with pytest.raises(AuthError, match="numeric"):
        check_production_auth_config("client", "sec", "admin_name")

    # Valid config passes
    check_production_auth_config("client", "sec", "12345678")


def test_demo_origin_scheme_mismatch():
    async def dummy_app(scope, receive, send):
        pass

    # 1. Base URL configured with 127.0.0.1
    s_127 = SecurityMiddleware(dummy_app, is_demo=True, public_base_url="http://127.0.0.1:8550")
    assert s_127._validate_origin("http://127.0.0.1:8550") is True
    # Different schemes or hosts are different origins under RFC 6454
    assert s_127._validate_origin("https://127.0.0.1:8550") is False
    assert s_127._validate_origin("http://localhost:8550") is False
    assert s_127._validate_origin("http://[::1]:8550") is False

    # Host header allows loopback variants in demo
    assert s_127._validate_host("127.0.0.1:8550") is True
    assert s_127._validate_host("[::1]:8550") is True
    assert s_127._validate_host("localhost:8550") is True
    assert s_127._validate_host("evil.com:8550") is False

    # 2. Base URL configured with IPv6 [::1]
    s_v6 = SecurityMiddleware(dummy_app, is_demo=True, public_base_url="http://[::1]:8550")
    assert s_v6._validate_origin("http://[::1]:8550") is True
    assert s_v6._validate_origin("http://127.0.0.1:8550") is False

    # 3. Base URL configured with localhost
    s_lh = SecurityMiddleware(dummy_app, is_demo=True, public_base_url="http://localhost:8550")
    assert s_lh._validate_origin("http://localhost:8550") is True
    assert s_lh._validate_origin("http://127.0.0.1:8550") is False


class AppMockPage:
    def __init__(self, auth=None):
        self.title = ""
        self.theme_mode = None
        self.padding = None
        self.scroll = None
        self.on_login = None
        self.on_disconnect = None
        self.on_connect = None
        self.on_close = None
        self.auth = auth
        self.controls = []
        self.navigation_bar = None
        self.updated_count = 0
        self.logged_out = False
        self.route = "/"
        self.scroll_offset = 0

    async def push_route(self, route):
        self.route = route

    async def scroll_to(self, *, offset, duration):
        self.scroll_offset = offset

    def add(self, *controls):
        self.controls.extend(controls)
        self.update()  # Flet Page.add pushes one update.

    def update(self):
        self.updated_count += 1

    def logout(self):
        self.logged_out = True


def app_controls(control):
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from app_controls(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from app_controls(content)


@pytest.mark.parametrize("user_id", ["12345678", 12345678, "other", "999", None, True])
def test_owner_login_and_logout(monkeypatch, user_id):
    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    actors = []

    def read(actor, root):
        actors.append(actor)
        return None

    monkeypatch.setattr(app, "load_board", read)

    async def run():
        page = AppMockPage(auth=MagicMock(user={"id": user_id}))
        await app.build_app()(page)
        event = ft.LoginEvent(name="login", control=None, error=None, error_description=None)
        try:
            await page.on_login(event)
            if user_id in ("12345678", 12345678):
                assert page.route == "/" and actors[0].is_valid
                assert any(
                    isinstance(c, ft.Text) and "还没有经过核验" in c.value
                    for c in app_controls(page.controls[0])
                )
                page.route = "/settings"
                await page.on_route_change(SimpleNamespace(route="/settings"))
                logout = next(
                    c
                    for c in app_controls(page.controls[0])
                    if isinstance(c, ft.Button) and c.content == "退出登录"
                )
                await logout.on_click(None)
                assert page.logged_out and not actors[0].is_valid and page.route == "/login"
            else:
                assert not actors and page.route == "/login"
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_oauth_uses_same_tab_authorization(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(app, "APP_MODE", "production")
    launcher = MagicMock(launch_url=AsyncMock())
    monkeypatch.setattr(ft, "UrlLauncher", lambda: launcher)

    async def run():
        page = AppMockPage()
        page.login = AsyncMock()
        await app.build_app()(page)
        try:
            login = next(
                c
                for c in app_controls(page.controls[0])
                if isinstance(c, ft.Button) and c.content == "使用 GitHub 登录"
            )
            await login.on_click(None)
            assert page.login.call_args.kwargs.get("redirect_to_page") is True
            callback = page.login.call_args.kwargs["on_open_authorization_url"]
            await callback("https://github.com/login/oauth/authorize?state=synthetic")
            launcher.launch_url.assert_awaited_once_with(
                "https://github.com/login/oauth/authorize?state=synthetic",
                web_only_window_name="_self",
            )
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_reconnect_revokes_expired_actor(monkeypatch):
    from a_stock_tracker import auth

    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    clock = [auth.time.monotonic()]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    actors = []
    monkeypatch.setattr(app, "load_board", lambda actor, root: actors.append(actor))

    async def run():
        page = AppMockPage(auth=MagicMock(user={"id": "12345678"}))
        await app.build_app()(page)
        try:
            await page.on_login(
                ft.LoginEvent(name="login", control=None, error=None, error_description=None)
            )
            await page.on_disconnect(None)
            clock[0] = actors[0].expires_at + 1
            await page.on_connect(None)
            assert len(actors) == 1 and not actors[0].is_valid
            assert any(
                isinstance(c, ft.Button) and c.content == "使用 GitHub 登录"
                for c in app_controls(page.controls[0])
            )
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_report_read_failure_has_retry_without_legacy_link(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    reads = []

    def broken(actor, root):
        reads.append(actor)
        raise ValueError("synthetic malformed batch")

    monkeypatch.setattr(app, "load_board", broken)

    async def run():
        page = AppMockPage(auth=MagicMock(user={"id": "12345678"}))
        await app.build_app()(page)
        try:
            await page.on_login(
                ft.LoginEvent(name="login", control=None, error=None, error_description=None)
            )
            controls = list(app_controls(page.controls[0]))
            retry = next(
                c for c in controls if isinstance(c, ft.Button) and c.content == "重新读取"
            )
            assert not any(
                "工作台" in str(getattr(c, "value", ""))
                or "兼容入口" in str(getattr(c, "content", ""))
                for c in controls
            )
            await retry.on_click(None)
            assert len(reads) == 2
            page.route = "/settings"
            await page.on_route_change(SimpleNamespace(route="/settings"))
            await retry.on_click(None)
            assert len(reads) == 2
        finally:
            await page.on_close(None)

    asyncio.run(run())


@pytest.mark.parametrize("route", ["/research", "/discover", "/company/600001.SH", "/jobs/old"])
def test_retired_page_state_never_restores_workbench(monkeypatch, route):
    monkeypatch.setattr(app, "APP_MODE", "demo")

    async def run():
        page = AppMockPage()
        page.route = route
        await app.build_app()(page)
        try:
            controls = list(app_controls(page.controls[0]))
            assert any(isinstance(c, ft.Text) and c.value == "页面不存在" for c in controls)
            assert page.navigation_bar is None
            back = next(
                c for c in controls if isinstance(c, ft.TextButton) and c.content == "返回波段机会"
            )
            await back.on_click(None)
            assert page.route == "/"
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_stale_logout_and_closed_reconnect_are_inert(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")

    async def run():
        page = AppMockPage()
        await app.build_app()(page)
        try:
            page.route = "/settings"
            await page.on_route_change(SimpleNamespace(route="/settings"))
            logout = next(
                c
                for c in app_controls(page.controls[0])
                if isinstance(c, ft.Button) and c.content == "退出登录"
            )
            page.route = "/"
            await page.on_route_change(SimpleNamespace(route="/"))
            await logout.on_click(None)
            assert not page.logged_out and page.route == "/"
            await page.on_close(None)
            updates = page.updated_count
            await page.on_connect(None)
            assert page.updated_count == updates
        finally:
            await page.on_close(None)

    asyncio.run(run())


@pytest.mark.parametrize(
    "changes, success",
    [
        (
            {
                "APP_MODE": "production",
                "GITHUB_CLIENT_ID": "synthetic",
                "GITHUB_CLIENT_SECRET": "synthetic",
                "GITHUB_ALLOWED_USER_ID": "12345678",
            },
            True,
        ),
        ({"APP_MODE": "production"}, False),
        ({"APP_MODE": "demo", "HOST": "0.0.0.0"}, False),
        ({"FLET_SESSION_TIMEOUT": "0"}, False),
        ({"APP_MODE": "unknown"}, False),
    ],
)
def test_startup_without_workspace_and_fail_closed(tmp_path, changes, success):
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "APP_MODE": "demo",
        "HOST": "127.0.0.1",
        "STATE_DIR": str(tmp_path / "does-not-exist"),
        "FLET_SESSION_TIMEOUT": "3600",
        "FLET_OAUTH_STATE_TIMEOUT": "600",
        "GITHUB_CLIENT_ID": "",
        "GITHUB_CLIENT_SECRET": "",
        "GITHUB_ALLOWED_USER_ID": "",
        **changes,
    }
    result = subprocess.run(
        [sys.executable, "-c", "import a_stock_tracker.app"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is success, result.stderr
    assert not list(tmp_path.iterdir())
