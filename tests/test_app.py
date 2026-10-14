"""Application authentication, lifecycle and ASGI security regressions."""

import asyncio
from pathlib import Path
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
    assert s._validate_origin("https://127.0.0.1:8550") is False
    assert s._validate_origin("http://localhost:8550") is False
    assert s._validate_origin("http://[::1]:8550") is False

    # Different port rejected in demo
    assert s._validate_origin("http://127.0.0.1:9999") is False

    # Invalid port strings and out-of-range ports rejected without exception
    assert s._validate_origin("http://127.0.0.1:bad") is False
    assert s._validate_origin("http://127.0.0.1:65536") is False
    assert s._validate_host("127.0.0.1:bad") is False
    assert s._validate_host("127.0.0.1:65536") is False

    # Host header allows loopback variants in demo
    assert s._validate_host("127.0.0.1:8550") is True
    assert s._validate_host("[::1]:8550") is True
    assert s._validate_host("localhost:8550") is True
    assert s._validate_host("evil.com:8550") is False

    # Base URL configured with IPv6 [::1]
    s_v6 = SecurityMiddleware(dummy_app, is_demo=True, public_base_url="http://[::1]:8550")
    assert s_v6._validate_origin("http://[::1]:8550") is True
    assert s_v6._validate_origin("http://127.0.0.1:8550") is False

    # Base URL configured with localhost
    s_lh = SecurityMiddleware(dummy_app, is_demo=True, public_base_url="http://localhost:8550")
    assert s_lh._validate_origin("http://localhost:8550") is True
    assert s_lh._validate_origin("http://127.0.0.1:8550") is False


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
        self.auth = None


def app_controls(control):
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from app_controls(child)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from app_controls(content)


async def completed_owner_authorization(user_id):
    from unittest.mock import patch

    from flet.auth.authorization_service import AuthorizationService
    from flet.auth.providers import GitHubOAuthProvider
    from flet.auth.user import User

    from a_stock_tracker.oauth_completion import OwnerAuthorization

    authorization = OwnerAuthorization(
        GitHubOAuthProvider("test-client", "test-secret", "http://localhost/callback"),
        fetch_user=True,
        fetch_groups=False,
    )

    async def fetch_user(code):
        authorization.user = User({"id": user_id}, user_id)

    with patch.object(AuthorizationService, "request_token", side_effect=fetch_user):
        await authorization.request_token("synthetic-code")
    return authorization


@pytest.mark.parametrize("user_id", ["12345678", 12345678, "other", "999", None, True])
def test_owner_login_and_logout(monkeypatch, user_id):
    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    actors = []

    def read(actor, root):
        actors.append(actor)
        return None

    monkeypatch.setattr(app, "OPPORTUNITY_ROOT", Path("/synthetic"))
    monkeypatch.setattr(app, "load_board", read)

    async def run():
        page = AppMockPage(auth=await completed_owner_authorization(user_id))
        await app.build_app()(page)
        event = ft.LoginEvent(name="login", control=None, error=None, error_description=None)
        try:
            await page.on_login(event)
            if user_id in ("12345678", 12345678):
                assert page.route == "/" and not actors
                await page.on_route_change(SimpleNamespace(route="/published"))
                assert actors[0].is_valid
                assert any(
                    isinstance(c, ft.Text) and "还没有价格参考批次" in c.value
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
            assert page.login.call_args.kwargs["authorization"] is app.OwnerAuthorization
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
    monkeypatch.setattr(app, "OPPORTUNITY_ROOT", Path("/synthetic"))
    monkeypatch.setattr(app, "load_board", lambda actor, root: actors.append(actor))

    async def run():
        page = AppMockPage(auth=await completed_owner_authorization("12345678"))
        await app.build_app()(page)
        try:
            await page.on_login(
                ft.LoginEvent(name="login", control=None, error=None, error_description=None)
            )
            await page.on_route_change(SimpleNamespace(route="/published"))
            await page.on_disconnect(None)
            clock[0] = actors[0].expires_at + 1
            await page.on_connect(None)
            assert len(actors) == 1 and not actors[0].is_valid
            assert page.auth is None and page.logged_out
            await page.on_login(
                ft.LoginEvent(name="login", control=None, error=None, error_description=None)
            )
            assert len(actors) == 1
            page.auth = await completed_owner_authorization("12345678")
            await page.on_login(
                ft.LoginEvent(name="login", control=None, error=None, error_description=None)
            )
            await page.on_route_change(SimpleNamespace(route="/published"))
            assert len(actors) == 2 and actors[-1].is_valid
            await page.on_login(
                ft.LoginEvent(name="login", control=None, error=None, error_description=None)
            )
            assert len(actors) == 2 and not actors[-1].is_valid
            assert any(
                isinstance(c, ft.Button) and c.content == "使用 GitHub 登录"
                for c in app_controls(page.controls[0])
            )
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_persistent_session_cookie_restores_owner_actor(monkeypatch):
    import time
    from unittest.mock import MagicMock

    from a_stock_tracker import auth

    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    monkeypatch.setattr(app, "GITHUB_CLIENT_SECRET", "test-secret-12345")

    token = auth.sign_session_token("12345678", "test-secret-12345", time.time() + 3600)

    page = AppMockPage()
    ws = MagicMock()
    ws.scope = {}
    ws.cookies = {"stock_session": token}
    conn = MagicMock()
    conn._FletApp__websocket = ws
    sess = MagicMock()
    sess.connection = conn
    page.session = sess

    async def run():
        await app.build_app()(page)
        assert not any(
            isinstance(c, ft.Button) and c.content == "使用 GitHub 登录"
            for c in app_controls(page.controls[0])
        )
        assert any(
            isinstance(c, ft.Text) and "研究首页" in c.value for c in app_controls(page.controls[0])
        )

    asyncio.run(run())


def test_multipage_session_revocation_and_tab_close(monkeypatch):
    import time
    from unittest.mock import MagicMock

    from a_stock_tracker import auth

    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    monkeypatch.setattr(app, "GITHUB_CLIENT_SECRET", "test-secret-12345")

    token = auth.sign_session_token("12345678", "test-secret-12345", time.time() + 3600)

    def make_page():
        page = AppMockPage()
        ws = MagicMock()
        ws.scope = {}
        ws.cookies = {"stock_session": token}
        conn = MagicMock()
        conn._FletApp__websocket = ws
        sess = MagicMock()
        sess.connection = conn
        page.session = sess
        return page

    async def run():
        page1 = make_page()
        page2 = make_page()
        await app.build_app()(page1)
        await app.build_app()(page2)

        actor1 = page1.session.connection._FletApp__websocket.scope.get(
            "actor"
        ) or auth.verify_session_token(token, "test-secret-12345", "12345678")
        assert actor1 is not None and actor1.is_valid

        # Tab close on page2 must NOT destroy the shared browser session
        await page2.on_close(None)
        assert actor1.is_valid
        assert auth.verify_session_token(token, "test-secret-12345", "12345678") is not None

        # Create unredeemed ticket for actor1's session, then logout
        ticket = auth.create_exchange_ticket("12345678", actor1.sid)

        # Explicit logout on page1 must revoke the shared session across all pages
        settings_route = SimpleNamespace(route="/settings")
        await page1.on_route_change(settings_route)
        logout = next(
            c
            for c in app_controls(page1.controls[0])
            if isinstance(c, ft.Button) and c.content == "退出登录"
        )
        await logout.on_click(None)

        # Actor 1 and any other actor sharing this sid is now invalid
        assert not actor1.is_valid
        assert auth.verify_session_token(token, "test-secret-12345", "12345678") is None

        # Unredeemed ticket was also purged; cannot create a new session
        assert auth.redeem_exchange_ticket(ticket) is None

        await page1.on_close(None)

    asyncio.run(run())


def test_repeated_rejected_callback_purges_ticket_and_sid(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    monkeypatch.setattr(app, "GITHUB_CLIENT_SECRET", "synthetic-secret")

    async def run():
        p = AppMockPage(auth=await completed_owner_authorization("12345678"))
        await app.build_app()(p)
        e = ft.LoginEvent(name="login", control=None, error=None, error_description=None)
        await p.on_login(e)
        ticket = next(
            c.src.split("ticket=")[1]
            for c in app_controls(p.controls[0])
            if isinstance(c, ft.Image) and "ticket=" in c.src
        )

        # Second login fails (fresh proof already consumed)
        await p.on_login(e)

        sent = []

        async def send(msg):
            sent.append(msg)

        middleware = app.SecurityMiddleware(None, False, "http://127.0.0.1:8550")
        await middleware(
            {
                "type": "http",
                "path": "/api/session",
                "query_string": f"ticket={ticket}".encode(),
                "headers": [(b"host", b"127.0.0.1:8550"), (b"sec-fetch-site", b"same-origin")],
            },
            None,
            send,
        )
        headers = dict(sent[0]["headers"])
        assert b"set-cookie" not in headers
        await p.on_close(None)

    asyncio.run(run())


def test_security_middleware_session_cookie_handling(monkeypatch):
    import time
    from unittest.mock import AsyncMock

    from a_stock_tracker import auth

    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    monkeypatch.setattr(app, "GITHUB_CLIENT_SECRET", "test-secret-12345")

    token = auth.sign_session_token("12345678", "test-secret-12345", time.time() + 3600)

    inner = AsyncMock()
    middleware = app.SecurityMiddleware(
        inner, is_demo=False, public_base_url="https://stock.keyi.win"
    )

    sent = []

    async def send(msg):
        sent.append(msg)

    async def run_api():
        scope = {
            "type": "http",
            "path": "/api/session",
            "query_string": f"token={token}".encode("utf-8"),
            "headers": [
                (b"host", b"stock.keyi.win"),
                (b"origin", b"https://stock.keyi.win"),
            ],
        }
        await middleware(scope, None, send)
        assert sent[0]["status"] == 200
        headers = dict(sent[0]["headers"])
        assert b"stock_session=" in headers[b"set-cookie"]
        assert b"Max-Age=28800" in headers[b"set-cookie"]

        sent.clear()
        scope["query_string"] = b"clear=1"
        scope["headers"] = [
            (b"host", b"stock.keyi.win"),
            (b"origin", b"https://stock.keyi.win"),
        ]
        await middleware(scope, None, send)
        assert sent[0]["status"] == 200
        headers = dict(sent[0]["headers"])
        assert b"Max-Age=0" in headers[b"set-cookie"]

        # 2c. Exchange ticket single-use redemption
        sent.clear()
        sid = auth.register_session("12345678", time.time() + 3600)
        ticket = auth.create_exchange_ticket("12345678", sid)
        scope["path"] = "/api/session"
        scope["query_string"] = f"ticket={ticket}".encode("utf-8")
        scope["headers"] = [
            (b"host", b"stock.keyi.win"),
            (b"origin", b"https://stock.keyi.win"),
            (b"sec-fetch-site", b"same-origin"),
        ]
        await middleware(scope, None, send)
        assert sent[0]["status"] == 200
        headers = dict(sent[0]["headers"])
        assert b"stock_session=" in headers[b"set-cookie"]

        sent.clear()
        # Redeeming the same ticket again fails (single use)
        await middleware(scope, None, send)
        assert sent[0]["status"] == 200
        headers = dict(sent[0]["headers"])
        assert b"set-cookie" not in headers

        # Redeeming a ticket whose sid was revoked fails
        sent.clear()
        revoked_sid = auth.register_session("12345678", time.time() + 3600)
        revoked_ticket = auth.create_exchange_ticket("12345678", revoked_sid)
        auth.ACTIVE_SESSIONS.pop(revoked_sid, None)
        scope["query_string"] = f"ticket={revoked_ticket}".encode("utf-8")
        await middleware(scope, None, send)
        assert sent[0]["status"] == 200
        headers = dict(sent[0]["headers"])
        assert b"set-cookie" not in headers

        # 2d. Cross-origin / same-site logout blocked (403) without session destruction
        sent.clear()
        cross_token = auth.sign_session_token("12345678", "test-secret-12345", time.time() + 3600)
        cross_scope = {
            "type": "http",
            "path": "/logout",
            "query_string": b"",
            "headers": [
                (b"host", b"stock.keyi.win"),
                (b"origin", b"https://attacker.example"),
                (b"sec-fetch-site", b"cross-site"),
                (b"cookie", f"stock_session={cross_token}".encode("utf-8")),
            ],
        }
        await middleware(cross_scope, None, send)
        assert sent[0]["status"] == 403
        assert auth.verify_session_token(cross_token, "test-secret-12345", "12345678") is not None

        # Same-site cross-subdomain logout blocked
        sent.clear()
        same_site_scope = {
            "type": "http",
            "path": "/logout",
            "query_string": b"",
            "headers": [
                (b"host", b"stock.keyi.win"),
                (b"origin", b"https://other.keyi.win"),
                (b"sec-fetch-site", b"same-site"),
                (b"cookie", f"stock_session={cross_token}".encode("utf-8")),
            ],
        }
        await middleware(same_site_scope, None, send)
        assert sent[0]["status"] == 403
        assert auth.verify_session_token(cross_token, "test-secret-12345", "12345678") is not None

        # Same-origin logout succeeds (303)
        sent.clear()
        scope["path"] = "/logout"
        scope["query_string"] = b""
        scope["headers"] = [
            (b"host", b"stock.keyi.win"),
            (b"origin", b"https://stock.keyi.win"),
            (b"sec-fetch-site", b"same-origin"),
            (b"cookie", f"stock_session={cross_token}".encode("utf-8")),
        ]
        await middleware(scope, None, send)
        assert sent[0]["status"] == 303
        headers = dict(sent[0]["headers"])
        assert headers[b"location"] == b"/login"
        assert b"Max-Age=0" in headers[b"set-cookie"]
        assert auth.verify_session_token(cross_token, "test-secret-12345", "12345678") is None

        ws_token = auth.sign_session_token("12345678", "test-secret-12345", time.time() + 3600)
        ws_scope = {
            "type": "websocket",
            "path": "/ws",
            "headers": [
                (b"host", b"stock.keyi.win"),
                (b"origin", b"https://stock.keyi.win"),
                (b"cookie", f"stock_session={ws_token}".encode("utf-8")),
            ],
        }
        inner.reset_mock()
        await middleware(ws_scope, None, send)
        inner.assert_awaited_once()
        assert "actor" in ws_scope
        assert ws_scope["actor"].user_id == "12345678"

    asyncio.run(run_api())


def test_report_read_failure_has_retry_without_legacy_link(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    reads = []

    def broken(actor, root):
        reads.append(actor)
        raise ValueError("synthetic malformed batch")

    monkeypatch.setattr(app, "OPPORTUNITY_ROOT", Path("/synthetic"))
    monkeypatch.setattr(app, "load_board", broken)

    async def run():
        page = AppMockPage(auth=await completed_owner_authorization("12345678"))
        await app.build_app()(page)
        try:
            await page.on_login(
                ft.LoginEvent(name="login", control=None, error=None, error_description=None)
            )
            await page.on_route_change(SimpleNamespace(route="/published"))
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
                c for c in controls if isinstance(c, ft.TextButton) and c.content == "返回研究首页"
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


@pytest.mark.parametrize("route", ["/", "/opportunity/600001.SH", "/analysis"])
def test_account_back_label_matches_contextual_navigation(monkeypatch, route):
    monkeypatch.setattr(app, "APP_MODE", "demo")

    async def run():
        page = AppMockPage()
        page.route = route
        await app.build_app()(page)
        try:
            account = next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.TextButton) and c.content == "账户"
            )
            await account.on_click(None)
            assert page.route == "/settings"
            back = next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.TextButton) and c.content == "返回上一页"
            )
            await back.on_click(None)
            assert page.route == route
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


def test_home_without_published_root_shows_research_and_never_reads_batch(monkeypatch):
    from a_stock_tracker.research_client import DemoResearchClient

    monkeypatch.setattr(app, "APP_MODE", "production")
    monkeypatch.setattr(app, "OPPORTUNITY_ROOT", None)
    monkeypatch.setattr(app, "GITHUB_ALLOWED_USER_ID", "12345678")
    monkeypatch.setattr(app, "configured_client", lambda: DemoResearchClient())
    monkeypatch.setattr(app, "load_board", lambda *a: pytest.fail("unconfigured batch read"))

    async def run():
        page = AppMockPage(auth=await completed_owner_authorization("12345678"))
        try:
            await app.build_app()(page)
            await page.on_login(
                ft.LoginEvent(name="login", control=None, error=None, error_description=None)
            )
            text = "\n".join(str(getattr(c, "value", "")) for c in app_controls(page))
            assert "进行中" not in text and "最近研究" in text
            assert "还没有价格参考批次" not in text
        finally:
            await page.on_close(None)

    asyncio.run(run())


@pytest.mark.parametrize("transition", ["reconnect", "settings", "home"])
def test_uncertain_financial_request_survives_rerender(monkeypatch, transition):
    from copy import deepcopy

    from a_stock_tracker.research_client import DemoResearchClient, ResearchUnavailable

    client = DemoResearchClient()
    monkeypatch.setattr(app, "APP_MODE", "demo")
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)
    original_submit = client.submit
    attempts = []

    def submit(payload):
        attempts.append(deepcopy(payload))
        accepted = original_submit(payload)
        if len(attempts) == 1:
            raise ResearchUnavailable("受理未知")
        return accepted

    monkeypatch.setattr(client, "submit", submit)

    async def run():
        page = AppMockPage()
        page.route = "/analysis?mode=financial&code=600519"
        await app.build_app()(page)
        try:
            consent = next(c for c in app_controls(page) if isinstance(c, ft.Checkbox))
            consent.value = True
            consent.on_change(None)
            await next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.Button) and c.content == "确认启动研究"
            ).on_click(None)
            if transition == "reconnect":
                await page.on_disconnect(None)
                await page.on_connect(None)
            elif transition == "settings":
                await next(
                    c
                    for c in app_controls(page)
                    if isinstance(c, ft.TextButton) and c.content == "账户"
                ).on_click(None)
                await next(
                    c
                    for c in app_controls(page)
                    if isinstance(c, ft.TextButton) and c.content == "返回上一页"
                ).on_click(None)
            else:
                await page.on_route_change(SimpleNamespace(route="/"))
                await page.on_route_change(
                    SimpleNamespace(route="/analysis?mode=financial&code=600900")
                )
            assert not any(isinstance(c, ft.Checkbox) for c in app_controls(page))
            await next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.Button) and c.content == "继续确认原请求"
            ).on_click(None)
            assert len(client.runs) == 1
            assert attempts[0] == attempts[1]
            assert page.route == "/analysis/" + attempts[0]["id"]
        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_repeated_account_entry_keeps_original_return(monkeypatch):
    monkeypatch.setattr(app, "APP_MODE", "demo")

    async def run():
        page = AppMockPage()
        page.route = "/analysis?mode=financial&code=600519"
        original = page.route
        await app.build_app()(page)
        try:
            account = next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.TextButton) and c.content == "账户"
            )
            await account.on_click(None)
            await account.on_click(None)
            await next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.TextButton) and c.content == "返回上一页"
            ).on_click(None)
            assert page.route == original
        finally:
            await page.on_close(None)

    asyncio.run(run())


@pytest.mark.parametrize(
    "route",
    ["/", "/published", "/opportunity/600001.SH", "/analysis/{id}", "/analysis/{id}/600001.SH"],
)
def test_open_price_snapshots_expire_locally_at_shanghai_midnight(monkeypatch, route):
    from copy import deepcopy
    from datetime import datetime, timedelta

    from a_stock_tracker import opportunity_demo
    from a_stock_tracker.research_client import DemoResearchClient
    from a_stock_tracker.research_service import quote
    from tests.test_opportunity_view import text_values

    client = DemoResearchClient()
    identifier = "a" * 32
    client.submit(
        dict(
            id=identifier,
            mode="swing",
            codes=["600001.SH"],
            confirmed=True,
            quote=quote(client.info(), "swing"),
        )
    )
    board = opportunity_demo.demo_board()
    expiry = board["rows"][0]["expires"]
    now = datetime.fromisoformat(expiry + "T23:59:59").replace(tzinfo=app.SHANGHAI)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(app, "datetime", Clock)
    monkeypatch.setattr(app, "APP_MODE", "demo")
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)
    monkeypatch.setattr(opportunity_demo, "demo_board", lambda: deepcopy(board))
    status = client.status
    reads = []

    def read(i):
        reads.append(i)
        return status(i)

    monkeypatch.setattr(client, "status", read)
    sleep = asyncio.sleep

    async def run():
        nonlocal now
        wake = asyncio.Event()
        ready = asyncio.Event()

        async def timer(delay):
            if delay != 5:
                ready.set()
                await wake.wait()
            else:
                await sleep(delay)

        monkeypatch.setattr(app.asyncio, "sleep", timer)
        page = AppMockPage()
        page.route = route.format(id=identifier)
        await app.build_app()(page)
        try:
            await asyncio.wait_for(ready.wait(), 2)
            before = text_values(page)
            assert "已过期" not in "\n".join(before)
            read_count = len(reads)
            now += timedelta(seconds=1)
            wake.set()
            for _ in range(5):
                await sleep(0)
            after = "\n".join(text_values(page))
            assert "已过期" in after
            assert "观察线与失效线" not in after
            assert "距离失效线" not in after
            assert len(reads) == read_count
            if route.endswith("600001.SH"):
                assert "均线" in after or "MA20" in after
        finally:
            await page.on_close(None)

    asyncio.run(run())


@pytest.mark.parametrize("resolution", ["quote_changed", "logout"])
def test_pending_request_rejection_and_logout_clear_confirmation(monkeypatch, resolution):
    from a_stock_tracker.research_client import (
        DemoResearchClient,
        ResearchQuoteChanged,
        ResearchUnavailable,
    )

    client = DemoResearchClient()
    monkeypatch.setattr(app, "APP_MODE", "demo")
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)
    attempts = []

    def submit(payload):
        attempts.append(payload)
        if len(attempts) == 1:
            raise ResearchUnavailable("受理未知")
        raise ResearchQuoteChanged("报价变化")

    monkeypatch.setattr(client, "submit", submit)

    async def run():
        page = AppMockPage()
        page.route = "/analysis?mode=financial"
        await app.build_app()(page)
        try:
            field = next(c for c in app_controls(page) if isinstance(c, ft.TextField))
            field.value = "600519"
            field.on_change(None)
            consent = next(c for c in app_controls(page) if isinstance(c, ft.Checkbox))
            consent.value = True
            consent.on_change(None)
            await next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.Button) and c.content == "确认启动研究"
            ).on_click(None)
            await page.on_disconnect(None)
            await page.on_connect(None)
            if resolution == "quote_changed":
                await next(
                    c
                    for c in app_controls(page)
                    if isinstance(c, ft.Button) and c.content == "继续确认原请求"
                ).on_click(None)
                assert attempts[0] == attempts[1]
                assert (
                    next(c for c in app_controls(page) if isinstance(c, ft.TextField)).value
                    == "600519"
                )
            else:
                await next(
                    c
                    for c in app_controls(page)
                    if isinstance(c, ft.TextButton) and c.content == "账户"
                ).on_click(None)
                await next(
                    c
                    for c in app_controls(page)
                    if isinstance(c, ft.Button) and c.content == "退出登录"
                ).on_click(None)
                await next(
                    c
                    for c in app_controls(page)
                    if isinstance(c, ft.Button) and c.content == "进入演示"
                ).on_click(None)
                await page.on_route_change(SimpleNamespace(route="/analysis"))
                assert len(attempts) == 1
            assert next(c for c in app_controls(page) if isinstance(c, ft.Checkbox)).value is False
            assert next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.Button) and c.content == "确认启动研究"
            ).disabled
        finally:
            await page.on_close(None)

    asyncio.run(run())


@pytest.mark.parametrize(
    "route, mode, label, multiline",
    [
        ("/analysis", "financial", "一家公司的股票代码", False),
        ("/analysis?mode=swing", "swing", "股票代码（1–5 家）", True),
        ("/analysis?mode=screen", "screen", "股票代码（1–5 家）", False),
    ],
)
def test_confirmation_is_focused_and_navigation_never_submits(
    monkeypatch, route, mode, label, multiline
):
    from a_stock_tracker.research_client import DemoResearchClient
    from tests.test_opportunity_view import text_values

    client = DemoResearchClient()
    monkeypatch.setattr(app, "APP_MODE", "demo")
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)
    monkeypatch.setattr(client, "submit", lambda _: pytest.fail("navigation submitted research"))

    async def run():
        page = AppMockPage()
        page.route = route
        await app.build_app()(page)
        try:
            controls = list(app_controls(page))
            assert next(c for c in controls if isinstance(c, ft.RadioGroup)).value == mode
            field = next(c for c in controls if isinstance(c, ft.TextField))
            assert field.label == label
            assert field.visible is (mode != "screen")
            if mode != "screen":
                assert field.multiline is multiline
            text = "\n".join(text_values(page))
            assert "最近研究" not in text and "进行中" not in text
            assert ("输出 token 与费用无硬上限" in text) is (mode == "financial")
            checkbox = next(c for c in controls if isinstance(c, ft.Checkbox))
            assert checkbox.value is False
            if mode != "financial":
                assert "不调用模型" in checkbox.label
            assert next(c for c in controls if isinstance(c, ft.Button)).disabled
            chooser = next(c for c in controls if isinstance(c, ft.ExpansionTile))
            assert chooser.title.value == "更换研究类型"
            checkbox.value = True
            checkbox.on_change(None)
            selected = next(c for c in controls if isinstance(c, ft.RadioGroup))
            selected.value = "financial" if mode != "financial" else "swing"
            selected.on_change(None)
            assert checkbox.value is False
            assert next(c for c in controls if isinstance(c, ft.Button)).disabled

        finally:
            await page.on_close(None)

    asyncio.run(run())


def test_unknown_before_receipt_shows_saved_costs_and_only_reuses_original_request(monkeypatch):
    from copy import deepcopy

    from a_stock_tracker.research_client import DemoResearchClient, ResearchUnavailable
    from tests.test_opportunity_view import text_values

    client = DemoResearchClient()
    monkeypatch.setattr(app, "APP_MODE", "demo")
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)
    submit = client.submit
    attempts = []

    def uncertain(payload):
        attempts.append(deepcopy(payload))
        if len(attempts) == 1:
            raise ResearchUnavailable("synthetic request never received")
        return submit(payload)

    monkeypatch.setattr(client, "submit", uncertain)

    async def run():
        page = AppMockPage()
        page.route = "/analysis?mode=financial&code=600519"
        await app.build_app()(page)
        try:
            checkbox = next(c for c in app_controls(page) if isinstance(c, ft.Checkbox))
            checkbox.value = True
            checkbox.on_change(None)
            await next(c for c in app_controls(page) if isinstance(c, ft.Button)).on_click(None)
            assert not client.runs
            text = "\n".join(text_values(page))
            assert "如果服务此前没有收到请求" in text
            assert "会启动研究并可能产生费用" in text
            assert "600519.SH" in text and "100 次数据请求 / 30 分钟" in text
            assert "输出 token 与费用无硬上限" in text
            assert attempts[0]["quote"]["model"]["model"] in text
            detail = next(c for c in app_controls(page) if isinstance(c, ft.ExpansionTile))
            assert attempts[0]["id"] in "\n".join(text_values(detail))
            assert not any(isinstance(c, ft.Checkbox) for c in app_controls(page))
            await next(
                c
                for c in app_controls(page)
                if isinstance(c, ft.Button) and c.content == "继续确认原请求"
            ).on_click(None)
            assert attempts[0] == attempts[1] and len(client.runs) == 1
        finally:
            await page.on_close(None)

    asyncio.run(run())


@pytest.mark.parametrize(
    "seconds, label", [(900, "15 分钟"), (1800, "30 分钟"), (3600, "60 分钟"), (66, "1 分钟 6 秒")]
)
def test_budget_duration_preserves_exact_confirmed_limit(seconds, label):
    assert app.budget_duration(seconds) == label


@pytest.mark.parametrize("source", ["financial_report", "expired_price"])
def test_result_price_navigation_prefills_code_without_submitting(monkeypatch, source):
    from a_stock_tracker import opportunity_demo
    from a_stock_tracker.research_client import DemoResearchClient

    client = DemoResearchClient()
    identifier = "c" * 32
    client.submit(
        {
            "id": identifier,
            "mode": "financial",
            "codes": ["600001.SH"],
            "confirmed": True,
            "quote": app.quote(client.info(), "financial"),
        }
    )
    board = opportunity_demo.demo_board()
    board["rows"][0]["expired"] = True
    monkeypatch.setattr(app, "APP_MODE", "demo")
    monkeypatch.setattr(app, "DemoResearchClient", lambda: client)
    monkeypatch.setattr(opportunity_demo, "demo_board", lambda: board)
    monkeypatch.setattr(client, "submit", lambda _: pytest.fail("navigation submitted research"))

    async def run():
        page = AppMockPage()
        page.route = (
            f"/analysis/{identifier}" if source == "financial_report" else "/opportunity/600001.SH"
        )
        await app.build_app()(page)
        try:
            label = "核对本公司价格参考" if source == "financial_report" else "重新核对价格参考"
            await next(
                c for c in app_controls(page) if isinstance(c, ft.TextButton) and c.content == label
            ).on_click(None)
            assert page.route == "/analysis?mode=swing&code=600001.SH"
            controls = list(app_controls(page))
            assert next(c for c in controls if isinstance(c, ft.RadioGroup)).value == "swing"
            field = next(c for c in controls if isinstance(c, ft.TextField))
            assert field.value == "600001.SH"
            assert field.label == "股票代码（1–5 家）"
            assert next(c for c in controls if isinstance(c, ft.Checkbox)).value is False
            start = next(
                c for c in controls if isinstance(c, ft.Button) and c.content == "确认启动研究"
            )
            assert start.disabled
            await start.on_click(None)
            assert len(client.runs) == 1
        finally:
            await page.on_close(None)

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["demo", "production"])
def test_first_page_update_uses_chinese_theme_without_font_transition(monkeypatch, mode):
    monkeypatch.setattr(app, "APP_MODE", mode)

    class FirstFramePage(AppMockPage):
        def update(self):
            assert self.theme.font_family == "NotoSansSC"
            assert self.theme_animation_style.duration.in_milliseconds == 0
            assert self.theme_animation_style.reverse_duration.in_milliseconds == 0
            super().update()

    async def run():
        page = FirstFramePage()
        await app.build_app()(page)
        try:
            assert page.updated_count > 0
        finally:
            await page.on_close(None)

    asyncio.run(run())
