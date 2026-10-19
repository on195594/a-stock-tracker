"""Authentication and request-boundary tests for the server-rendered Web.

Mapping from the retired Flet tests/test_app.py security cases:
- asgi_demo_request_boundary / strict_host_and_origin / demo_origin_and_port
    -> test_host_header_is_strict, test_post_requires_same_origin_and_csrf
- production_auth_checks / startup_without_workspace_and_fail_closed
    -> test_production_configuration_fails_closed, test_web_never_reads_research_root
- owner_login_and_logout / oauth_uses_same_tab / repeated_rejected_callback
    -> test_oauth_state_is_single_use_and_browser_bound, test_unlisted_github_user_is_denied,
       test_logout_requires_post_csrf_and_revokes_every_copy
- reconnect_revokes_expired_actor / persistent_session_cookie / multipage_revocation /
  stale_logout_and_closed_reconnect
    -> test_expired_or_tampered_session_is_refused, test_logout_requires_post_csrf_and_revokes_every_copy,
       test_fresh_login_replaces_older_sessions
- security_middleware_session_cookie_handling / retired_page_state_never_restores_workbench
    -> test_retired_routes_are_gone, test_session_cookie_attributes
- uncertain_financial_request_survives_rerender / unknown_before_receipt_shows_saved_costs
    -> test_acceptance_unknown_reconciles_only_the_same_request
- pending_request_rejection_and_logout_clear_confirmation / confirmation_focused_and_navigation_never_submits
    -> test_quote_change_keeps_input_and_clears_confirmation, test_get_never_submits,
       test_unconfirmed_request_is_refused, test_in_flight_revocation_hides_late_results
- open_price_snapshots_expire_locally_at_shanghai_midnight
    -> tests/test_web_pages.py expiry tests and the browser regression
- websocket_origin_check / first_page_update_uses_chinese_theme
    -> retired with Flet (no WebSocket, system fonts); test_websocket_is_closed
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from a_stock_tracker.auth import ACTIVE_SESSIONS, AuthError
from a_stock_tracker.research_client import (
    ResearchClient,
    ResearchQuoteChanged,
    ResearchRejected,
    ResearchUnavailable,
)
from a_stock_tracker.web import session as s
from a_stock_tracker.web.routes import create_app
from a_stock_tracker.web.security import CSP
from a_stock_tracker.web.settings import Settings, settings_from_env
from tests.web_support import (
    BASE,
    OWNER,
    SAME,
    FakeClient,
    app_client,
    csrf_of,
    form_fields,
    login,
    production,
    request_form,
    reset_sessions,
)


@pytest.fixture(autouse=True)
def clean_sessions():
    reset_sessions()
    yield
    reset_sessions()


@pytest.mark.parametrize(
    "overrides",
    [
        {"github_client_id": ""},
        {"github_client_secret": " "},
        {"allowed_user_id": ""},
        {"allowed_user_id": "octocat"},
        {"public_base_url": "http://stock.example"},
        {"public_base_url": "https://stock.example/app"},
    ],
)
def test_production_configuration_fails_closed(overrides):
    values = {
        "mode": "production",
        "public_base_url": BASE,
        "github_client_id": "client",
        "github_client_secret": "secret",
        "allowed_user_id": OWNER,
    }
    with pytest.raises((AuthError, ValueError)):
        Settings(**(values | overrides)).validate()


def test_demo_is_loopback_only_and_mode_is_explicit(monkeypatch):
    with pytest.raises(ValueError):
        Settings(mode="demo", public_base_url="http://127.0.0.1:8551", host="0.0.0.0").validate()
    with pytest.raises(ValueError):
        Settings(mode="demo", public_base_url="http://stock.example").validate()
    monkeypatch.setenv("APP_MODE", "staging")
    with pytest.raises(ValueError):
        settings_from_env()


def test_web_never_reads_research_root():
    import a_stock_tracker.web.routes as routes

    source = open(routes.__file__, encoding="utf-8").read()
    for marker in ("RESEARCH_SERVICE_ROOT", "load_board", "OPPORTUNITY_ROOT", "TUSHARE"):
        assert marker not in source


@pytest.mark.parametrize(
    "host,ok",
    [
        ("stock.example", True),
        ("stock.example:443", True),
        ("STOCK.example", True),
        ("evil.example", False),
        ("stock.example:8443", False),
        ("stock.example.evil", False),
        ("", False),
        ("stock.example:x", False),
        ("[::1]", False),
    ],
)
def test_host_header_is_strict(host, ok):
    browser = app_client()
    response = browser.get("/login", headers={"host": host})
    assert (response.status_code == 200) is ok


def test_demo_host_requires_loopback_and_port():
    settings = Settings(mode="demo", public_base_url="http://127.0.0.1:8551", port=8551).validate()
    browser = TestClient(create_app(settings, FakeClient()), base_url="http://127.0.0.1:8551")
    assert browser.get("/login").status_code == 200
    assert browser.get("/login", headers={"host": "localhost:8551"}).status_code == 200
    assert browser.get("/login", headers={"host": "[::1]:8551"}).status_code == 200
    assert browser.get("/login", headers={"host": "127.0.0.1:8550"}).status_code == 403
    assert browser.get("/login", headers={"host": "192.168.1.2:8551"}).status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/research",
        "/research/x",
        "/discover",
        "/company/600519.SH",
        "/jobs/1",
        "/upload",
        "/published",
        "/opportunity/600519.SH",
        "/api/session",
    ],
)
def test_retired_routes_are_gone(path):
    browser = app_client()
    login(browser)
    response = browser.get(path)
    assert response.status_code == 404 and "研究首页" not in response.text


def test_websocket_is_closed():
    browser = app_client()
    with pytest.raises(WebSocketDisconnect):
        with browser.websocket_connect("/ws"):
            pass


def test_security_headers_and_cache_policy():
    browser = app_client()
    login(browser)
    page = browser.get("/")
    assert page.headers["content-security-policy"] == CSP
    assert "'unsafe-inline'" not in CSP and "'unsafe-eval'" not in CSP
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"
    assert "max-age" in page.headers["strict-transport-security"]
    assert "<script>" not in page.text and " style=" not in page.text
    asset = browser.get("/static/app.css")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]


def test_early_data_cannot_carry_side_effects():
    client = FakeClient()
    browser = app_client(client)
    login(browser)
    fields = request_form(browser, "financial", "600519")
    refused = browser.post("/analysis", data=fields, headers={**SAME, "early-data": "1"})
    assert refused.status_code == 425 and not client.submitted
    assert browser.get("/", headers={"early-data": "1"}).status_code == 200


def test_oauth_state_is_single_use_and_browser_bound():
    browser = app_client()
    start = browser.get("/oauth/start", follow_redirects=False)
    assert start.status_code == 303
    location = start.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    assert "redirect_uri=https%3A%2F%2Fstock.example%2Foauth_callback" in location
    state = location.split("state=")[1].split("&")[0]
    # Another browser cannot complete this state (login CSRF).
    stranger = app_client()
    other = stranger.get(f"/oauth_callback?code=c&state={state}", follow_redirects=False)
    assert other.headers["location"] == "/login?error=state" and s.COOKIE not in stranger.cookies
    # The state was consumed by that attempt, so the owner must start again.
    replay = browser.get(f"/oauth_callback?code=c&state={state}", follow_redirects=False)
    assert replay.headers["location"] == "/login?error=state"
    assert s.COOKIE not in browser.cookies
    login(browser)
    assert browser.get("/").status_code == 200


def test_oauth_state_capacity_preserves_pending_login_and_recovers(monkeypatch):
    monkeypatch.setattr(s, "MAX_OAUTH_STATES", 2)
    browser = app_client()
    browser.get("/oauth/start", follow_redirects=False)
    state = browser.cookies[s.STATE_COOKIE]
    stranger = app_client()
    stranger.get("/oauth/start", follow_redirects=False)
    pending = dict(s.OAUTH_STATES)
    for _ in range(3):
        refused = stranger.get("/oauth/start", follow_redirects=False)
        assert refused.status_code == 429
        assert "稍后重试" in refused.text
        assert "set-cookie" not in refused.headers
        assert refused.headers["cache-control"] == "no-store"
        assert s.OAUTH_STATES == pending

    done = browser.get(f"/oauth_callback?code=c&state={state}", follow_redirects=False)
    assert done.headers["location"] == "/" and s.COOKIE in browser.cookies
    assert stranger.get("/oauth/start", follow_redirects=False).status_code == 303
    assert len(s.OAUTH_STATES) == 2
    monkeypatch.setattr(s.time, "time", lambda: max(pending.values()) + s.STATE_SECONDS)
    assert stranger.get("/oauth/start", follow_redirects=False).status_code == 303
    assert len(s.OAUTH_STATES) == 1


def test_expired_oauth_state_is_refused():
    browser = app_client()
    start = browser.get("/oauth/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    s.OAUTH_STATES[state] = time.time() - 1
    done = browser.get(f"/oauth_callback?code=c&state={state}", follow_redirects=False)
    assert done.headers["location"] == "/login?error=state"


def test_unlisted_github_user_is_denied():
    browser = app_client(exchange=lambda code, settings: 7)
    start = browser.get("/oauth/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    done = browser.get(f"/oauth_callback?code=c&state={state}", follow_redirects=False)
    assert done.headers["location"] == "/login?error=denied"
    assert s.COOKIE not in browser.cookies and not ACTIVE_SESSIONS
    assert "该 GitHub 账号未获授权" in browser.get("/login?error=denied").text


@pytest.mark.parametrize("value", [None, {"id": 1}, 1.5])
def test_malformed_github_identity_or_exchange_failure_is_refused(value):
    def exchange(code, settings):
        if value is None:
            raise ValueError("bad json")
        return value

    browser = app_client(exchange=exchange)
    start = browser.get("/oauth/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    done = browser.get(f"/oauth_callback?code=c&state={state}", follow_redirects=False)
    assert done.headers["location"] == "/login?error=failed" and not ACTIVE_SESSIONS


def test_session_cookie_attributes():
    browser = app_client()
    start = browser.get("/oauth/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    done = browser.get(f"/oauth_callback?code=c&state={state}", follow_redirects=False)
    cookies = done.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith(s.COOKIE + "="))
    for attribute in ("HttpOnly", "SameSite=lax", "Secure", "Path=/", "Max-Age=28800"):
        assert attribute.lower() in session.lower()
    cleared = next(c for c in cookies if c.startswith(s.STATE_COOKIE + "="))
    assert "max-age=0" in cleared.lower()


def test_unauthenticated_pages_redirect_and_json_is_401():
    browser = app_client()
    for path in ("/", "/analysis", "/settings", "/analysis/" + "a" * 32):
        response = browser.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
    state = browser.get("/analysis/" + "a" * 32 + "/state.json")
    assert state.status_code == 401 and state.json() == {"error": "unauthorized"}


def test_expired_or_tampered_session_is_refused():
    browser = app_client()
    token = login(browser)
    payload, signature = token.rsplit(".", 1)
    browser.cookies.set(s.COOKIE, payload + "." + ("0" * len(signature)))
    assert browser.get("/", follow_redirects=False).status_code == 303
    browser.cookies.set(s.COOKIE, token)
    assert browser.get("/", follow_redirects=False).status_code == 200
    sid = next(iter(ACTIVE_SESSIONS))
    ACTIVE_SESSIONS[sid] = (OWNER, time.time() - 1)
    assert browser.get("/", follow_redirects=False).status_code == 303


def test_fresh_login_replaces_older_sessions():
    first, second = app_client(), app_client()
    login(first)
    login(second)
    assert first.get("/", follow_redirects=False).status_code == 303
    assert second.get("/", follow_redirects=False).status_code == 200


def test_logout_requires_post_csrf_and_revokes_every_copy():
    browser = app_client()
    token = login(browser)
    other_tab = app_client()
    other_tab.cookies.set(s.COOKIE, token)
    assert browser.get("/logout").status_code == 405
    forged = browser.post("/logout", data={"csrf": "x"}, headers=SAME)
    assert forged.status_code == 403
    cross = browser.post("/logout", data={"csrf": csrf_of(browser.get("/settings").text)})
    assert cross.status_code == 403
    assert other_tab.get("/", follow_redirects=False).status_code == 200
    done = browser.post(
        "/logout",
        data={"csrf": csrf_of(browser.get("/settings").text)},
        headers=SAME,
        follow_redirects=False,
    )
    assert done.status_code == 303 and done.headers["location"] == "/login"
    assert other_tab.get("/", follow_redirects=False).status_code == 303
    browser.cookies.set(s.COOKIE, token)
    assert browser.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"origin": "https://evil.example"},
        {"origin": "http://stock.example"},
        {"origin": BASE, "sec-fetch-site": "cross-site"},
        {"referer": "https://evil.example/analysis"},
        {"origin": "null"},
    ],
)
def test_post_requires_same_origin_and_csrf(headers):
    client = FakeClient()
    browser = app_client(client)
    login(browser)
    fields = request_form(browser, "financial", "600519")
    assert browser.post("/analysis", data=fields, headers=headers).status_code == 403
    no_token = {k: v for k, v in fields.items() if k != "csrf"}
    assert browser.post("/analysis", data=no_token, headers=SAME).status_code == 403
    assert not client.submitted
    referer = browser.post(
        "/analysis", data=fields, headers={"referer": BASE + "/analysis"}, follow_redirects=False
    )
    assert referer.status_code == 303 and len(client.submitted) == 1


def test_get_never_submits():
    client = FakeClient()
    browser = app_client(client)
    login(browser)
    for path in (
        "/analysis?mode=financial&code=600519&confirmed=on",
        "/analysis?mode=swing&code=600519",
        "/analysis?mode=screen",
    ):
        assert browser.get(path).status_code == 200
    assert not client.submitted


def test_unconfirmed_request_is_refused():
    client = FakeClient()
    browser = app_client(client)
    login(browser)
    fields = request_form(browser, "financial", "600519")
    del fields["confirmed"]
    response = browser.post("/analysis", data=fields, headers=SAME)
    assert response.status_code == 400 and "请先勾选确认" in response.text
    assert not client.submitted


def test_quote_change_keeps_input_and_clears_confirmation():
    client = FakeClient()
    client.fail["submit"] = ResearchQuoteChanged("服务预算或模型已变化，请重新确认")
    browser = app_client(client)
    login(browser)
    fields = request_form(browser, "financial", "600519")
    response = browser.post("/analysis", data=fields, headers=SAME)
    assert response.status_code == 409
    assert "已刷新报价并保留输入" in response.text and 'value="600519"' in response.text
    assert "checked" not in response.text
    assert form_fields(response.text)["id"] != fields["id"]


def test_rejection_keeps_input_with_reason():
    client = FakeClient()
    client.fail["submit"] = ResearchRejected("该研究类型尚未配置行情或模型服务")
    browser = app_client(client)
    login(browser)
    response = browser.post(
        "/analysis", data=request_form(browser, "swing", "600519,000001"), headers=SAME
    )
    assert response.status_code == 400 and "尚未配置行情或模型服务" in response.text
    assert "600519,000001" in response.text


@pytest.mark.parametrize("invalid_receipt", [False, True])
def test_acceptance_unknown_reconciles_only_the_same_request(monkeypatch, invalid_receipt):
    client = FakeClient()
    original_submit = client.submit
    if invalid_receipt:
        real_client = ResearchClient("http://research", "x" * 32)

        def accepted_without_status(_method, _path, payload):
            return original_submit(payload).model_dump(exclude={"status"})

        monkeypatch.setattr(real_client, "call", accepted_without_status)
        monkeypatch.setattr(client, "submit", real_client.submit)
    else:
        client.fail["submit"] = ResearchUnavailable("研究服务或受理状态暂不可确认。")
    browser = app_client(client)
    login(browser)
    fields = request_form(browser, "financial", "600519")
    unknown = browser.post("/analysis", data=fields, headers=SAME)
    assert unknown.status_code == 503
    assert "核对也可能启动原请求" in unknown.text and fields["id"] in unknown.text
    assert "输出 token 与费用无硬上限" in unknown.text
    client.fail.pop("submit", None)
    monkeypatch.setattr(client, "submit", original_submit)
    again = form_fields(unknown.text)
    done = browser.post(
        "/analysis/reconcile",
        data={"csrf": again["csrf"], "payload": again["payload"], "codes": "600519"},
        headers=SAME,
        follow_redirects=False,
    )
    assert done.status_code == 303 and done.headers["location"] == "/analysis/" + fields["id"]
    assert client.submitted[0] == client.submitted[1]
    assert client.submitted[1]["id"] == fields["id"]
    assert list(client.demo.runs) == [fields["id"]]


def test_in_flight_revocation_hides_late_results():
    client = FakeClient()
    browser = app_client(client)
    login(browser)
    run = browser.post(
        "/analysis",
        data=request_form(browser, "financial", "600519"),
        headers=SAME,
        follow_redirects=False,
    ).headers["location"]
    entered, release = threading.Event(), threading.Event()
    original = client.status

    def slow(identifier):
        entered.set()
        assert release.wait(5)
        return original(identifier)

    client.status = slow
    outcome: dict = {}
    thread = threading.Thread(
        target=lambda: outcome.update(page=browser.get(run, follow_redirects=False))
    )
    thread.start()
    assert entered.wait(5)
    ACTIVE_SESSIONS.clear()  # logout or expiry elsewhere while the read is blocked
    release.set()
    thread.join(5)
    page = outcome["page"]
    assert page.status_code == 303 and page.headers["location"] == "/login"
    assert "最强反证" not in page.text


def test_demo_accepts_loopback_alias_origins_only():
    settings = Settings(mode="demo", public_base_url="http://127.0.0.1:8551", port=8551).validate()
    app = create_app(settings, FakeClient())
    for host in ("localhost:8551", "127.0.0.1:8551", "[::1]:8551"):
        browser = TestClient(app, base_url=f"http://{host}")
        ok = browser.post(
            "/login/demo", headers={"origin": f"http://{host}"}, follow_redirects=False
        )
        assert ok.status_code == 303, host
    browser = TestClient(app, base_url="http://localhost:8551")
    for origin in ("http://localhost:8550", "http://evil.example:8551", "https://localhost:8551"):
        refused = browser.post("/login/demo", headers={"origin": origin}, follow_redirects=False)
        assert refused.status_code == 403, origin


ADMIN = {"admin_username": "owner", "admin_password": "p" * 16}
LOCAL = {"x-real-ip": "172.24.0.1"}
PUBLIC = {"x-real-ip": "104.22.100.151"}


def admin_post(browser: TestClient, password: str, ip: dict[str, str], **headers: str):
    return browser.post(
        "/login/admin",
        data={"username": "owner", "password": password},
        headers=SAME | ip | headers,
        follow_redirects=False,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"admin_password": "p" * 15, "admin_username": "owner"},
        {"admin_password": "p" * 16, "admin_username": " "},
        {"admin_scope": "public"},
        {"admin_allowed_ips": ("150.136.58.125", "not-an-ip")},
    ],
)
def test_admin_login_configuration_fails_closed(overrides):
    with pytest.raises(ValueError):
        production(**overrides)


def test_admin_login_is_off_without_a_password():
    browser = app_client()
    assert "/login/admin" not in browser.get("/login", headers=LOCAL).text
    assert admin_post(browser, "p" * 16, LOCAL).status_code == 404
    demo = Settings(mode="demo", public_base_url="http://127.0.0.1:8551", port=8551).validate()
    local = TestClient(create_app(demo, FakeClient()), base_url="http://127.0.0.1:8551")
    assert (
        local.post("/login/admin", headers={"origin": "http://127.0.0.1:8551"}).status_code == 404
    )


def test_admin_login_issues_the_owner_session_only_for_the_right_password():
    browser = app_client(settings=production(**ADMIN))
    assert "/login/admin" in browser.get("/login", headers=LOCAL).text
    wrong = admin_post(browser, "q" * 16, LOCAL)
    assert wrong.headers["location"] == "/login?error=admin" and s.COOKIE not in browser.cookies
    assert admin_post(browser, "p" * 16, LOCAL, origin="https://evil.example").status_code == 403
    assert s.COOKIE not in browser.cookies
    done = admin_post(browser, "p" * 16, LOCAL)
    assert done.status_code == 303 and done.headers["location"] == "/"
    assert [user for user, _ in ACTIVE_SESSIONS.values()] == [OWNER]
    assert browser.get("/", follow_redirects=False).status_code == 200
    # Same owner session: CSRF and logout revocation apply unchanged.
    logout = browser.post(
        "/logout",
        data={"csrf": csrf_of(browser.get("/settings").text)},
        headers=SAME,
        follow_redirects=False,
    )
    assert logout.status_code == 303 and not ACTIVE_SESSIONS


@pytest.mark.parametrize("scope, public_ok", [("local", False), ("any", True)])
def test_admin_login_scope_limits_public_clients(scope, public_ok):
    browser = app_client(settings=production(**ADMIN, admin_scope=scope))
    assert ("/login/admin" in browser.get("/login", headers=PUBLIC).text) is public_ok
    result = admin_post(browser, "p" * 16, PUBLIC)
    assert result.status_code == (303 if public_ok else 404)
    assert (s.COOKIE in browser.cookies) is public_ok
    # Without a forwarded address the test client's peer is not an IP, so it is not local.
    assert ("/login/admin" in app_client(settings=production(**ADMIN)).get("/login").text) is False


def test_admin_login_allowlist_admits_only_listed_public_addresses():
    settings = production(**ADMIN, admin_allowed_ips=("150.136.58.125", "203.0.113.0/24"))
    for ip, ok in (("150.136.58.125", True), ("203.0.113.9", True), ("150.136.58.126", False)):
        browser = app_client(settings=settings)
        result = admin_post(browser, "p" * 16, {"x-real-ip": ip})
        assert result.status_code == (303 if ok else 404), ip
        assert (s.COOKIE in browser.cookies) is ok, ip
