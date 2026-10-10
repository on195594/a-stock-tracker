"""Authenticated, read-only Flet Web application for swing opportunities."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from a_stock_tracker.auth import (
    Actor,
    AuthError,
    check_production_auth_config,
    create_demo_actor,
    create_owner_actor,
)
from a_stock_tracker.opportunities import load_board
from a_stock_tracker.opportunity_view import (
    ACCENT,
    BACKGROUND,
    BAND,
    ERROR,
    INK,
    heading,
    notice,
    opportunity_view,
    paragraph,
    window_theme,
)

RAW_MODE = os.getenv("APP_MODE", "demo")
if RAW_MODE not in ("demo", "production"):
    raise ValueError(f"Invalid APP_MODE: '{RAW_MODE}'. Must be 'demo' or 'production'.")
APP_MODE: Literal["demo", "production"] = "production" if RAW_MODE == "production" else "demo"
OPPORTUNITY_ROOT = Path(os.environ["OPPORTUNITY_ROOT"]) if os.getenv("OPPORTUNITY_ROOT") else None
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8550")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8550"))

os.environ.setdefault("FLET_SESSION_TIMEOUT", "3600")
os.environ.setdefault("FLET_OAUTH_STATE_TIMEOUT", "600")
FLET_SESSION_TIMEOUT = int(os.environ["FLET_SESSION_TIMEOUT"])
FLET_OAUTH_STATE_TIMEOUT = int(os.environ["FLET_OAUTH_STATE_TIMEOUT"])
if FLET_SESSION_TIMEOUT <= 0 or FLET_OAUTH_STATE_TIMEOUT <= 0:
    raise ValueError("FLET_SESSION_TIMEOUT and FLET_OAUTH_STATE_TIMEOUT must be positive integers.")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_ALLOWED_USER_ID = os.getenv("GITHUB_ALLOWED_USER_ID", "")
if APP_MODE == "production":
    check_production_auth_config(GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_ALLOWED_USER_ID)
elif HOST not in ("127.0.0.1", "localhost", "::1"):
    raise ValueError(f"Demo mode only allows loopback host, got: {HOST}")


def build_app():
    import flet as ft

    async def main(page: ft.Page):
        page.title = "波段机会"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.fonts = {"NotoSansSC": "fonts/NotoSansSC-Regular.otf"}
        page.theme = window_theme()
        page.bgcolor = BACKGROUND
        page.padding = 0
        page.scroll = ft.ScrollMode.AUTO
        state: dict[str, Any] = {
            "route": page.route or "/",
            "actor": create_demo_actor() if APP_MODE == "demo" else None,
            "generation": 0,
            "connected": True,
            "closed": False,
            "settings_return": "/",
        }
        login_message = ft.Text("", color=ERROR, size=16)
        content_container = ft.Container()
        settings_button = ft.TextButton("账户", icon=ft.Icons.SETTINGS, height=48)

        def current(gen: int, actor: Actor | None) -> bool:
            return bool(
                state["connected"]
                and gen == state["generation"]
                and actor is state["actor"]
                and actor
                and actor.is_valid
            )

        def revoke():
            if state["actor"]:
                state["actor"].revoke()
            state["actor"] = None
            state["generation"] += 1

        async def navigate(route: str, *, from_browser: bool = False):
            state["generation"] += 1
            state["route"] = route
            if not from_browser:
                await page.push_route(route)
            await render()
            if state["connected"]:
                page.update()
                try:
                    await asyncio.wait_for(page.scroll_to(offset=0, duration=0), timeout=1)
                except Exception as exc:
                    logging.getLogger(__name__).debug(
                        "Scroll reset skipped: %s", type(exc).__name__
                    )

        def open_route(route: str, gen: int, actor: Actor | None):
            async def click(e):
                if current(gen, actor):
                    await navigate(route)

            return click

        provider = None
        if APP_MODE == "production":
            from flet.auth.providers import GitHubOAuthProvider

            provider = GitHubOAuthProvider(
                client_id=GITHUB_CLIENT_ID,
                client_secret=GITHUB_CLIENT_SECRET,
                redirect_url=f"{PUBLIC_BASE_URL}/oauth_callback",
            )

            async def on_open_auth_url(url: str):
                await ft.UrlLauncher().launch_url(url, web_only_window_name="_self")

            async def on_login(e: ft.LoginEvent):
                if not state["connected"]:
                    return
                revoke()
                if e.error:
                    login_message.value = "登录失败，请重新授权。"
                else:
                    user = getattr(page.auth, "user", None)
                    raw_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
                    try:
                        state["actor"] = create_owner_actor(raw_id, GITHUB_ALLOWED_USER_ID)
                    except AuthError:
                        login_message.value = "登录被拒绝：仅允许配置的所有者。"
                    else:
                        login_message.value = ""
                        await navigate("/")
                        return
                await navigate("/login")

            page.on_login = on_login

        async def login(e):
            if not state["connected"]:
                return
            if APP_MODE == "demo":
                state["actor"] = create_demo_actor()
                await navigate("/")
            else:
                await page.login(
                    provider,
                    on_open_authorization_url=on_open_auth_url,
                    redirect_to_page=True,
                )

        async def logout(e):
            if not state["connected"]:
                return
            revoke()
            page.logout()
            await navigate("/login")

        async def render():
            if not state["connected"]:
                return
            gen, actor = state["generation"], state["actor"]
            route = urlparse(state["route"]).path
            if actor and not actor.is_valid:
                revoke()
                gen, actor = state["generation"], None
            if not actor:
                route = "/login"
            settings_button.visible = route != "/login"
            if route == "/login":

                async def login_current(e):
                    if state["connected"] and gen == state["generation"]:
                        await login(e)

                content_container.content = ft.Column(
                    [
                        heading("登录后核查观察", size=30, level=1),
                        notice(
                            "只读研究入口",
                            "生产模式需要所有者登录以访问私人数据"
                            if APP_MODE == "production"
                            else "仅本地合成演示",
                        ),
                        paragraph("查看解释与反证，再核对价格条件；页面不会采集行情或调用模型。"),
                        ft.Button(
                            "使用 GitHub 登录" if APP_MODE == "production" else "进入演示",
                            on_click=login_current,
                            height=48,
                        ),
                        login_message,
                    ],
                    spacing=24,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                )
                return
            content_container.content = ft.Row(
                [ft.ProgressRing(width=20, height=20, stroke_width=2), paragraph("正在读取报告…")],
                spacing=16,
            )
            if state["connected"]:
                try:
                    page.update()
                except Exception as exc:
                    logging.getLogger(__name__).debug(
                        "Progress update skipped: %s", type(exc).__name__
                    )
            try:
                if route == "/" or route.startswith("/opportunity/"):
                    if APP_MODE == "demo":
                        from a_stock_tracker.opportunity_demo import demo_board

                        board = demo_board()
                    else:
                        board = await asyncio.to_thread(load_board, actor, OPPORTUNITY_ROOT)
                    code = route.rsplit("/", 1)[-1] if route.startswith("/opportunity/") else None
                    content = opportunity_view(
                        board,
                        code,
                        lambda company: open_route("/opportunity/" + company, gen, actor),
                        open_route("/", gen, actor),
                    )
                elif route == "/settings":

                    async def logout_current(e):
                        if current(gen, actor):
                            await logout(e)

                    content = ft.Column(
                        [
                            ft.TextButton(
                                "返回波段机会",
                                on_click=open_route(state["settings_return"], gen, actor),
                                height=48,
                            ),
                            heading("账户与运行信息", size=30, level=1),
                            notice("当前会话", f"当前模式: {APP_MODE}\n用户身份: {actor.user_id}"),
                            paragraph("Web 只读已发布批次；退出后隐藏当前会话内容。"),
                            ft.Button(
                                "退出登录", icon=ft.Icons.LOGOUT, on_click=logout_current, height=48
                            ),
                        ],
                        spacing=24,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    )
                else:
                    content = ft.Column(
                        [
                            heading("页面不存在", size=30, level=1),
                            ft.TextButton(
                                "返回波段机会", on_click=open_route("/", gen, actor), height=48
                            ),
                        ]
                    )
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Report read unavailable (%s)", type(exc).__name__
                )

                async def retry(e):
                    if current(gen, actor):
                        await render()
                        if state["connected"]:
                            page.update()

                content = ft.Column(
                    [
                        heading("报告暂时不可读", size=30, level=1),
                        notice(
                            "读取失败", "暂时无法读取报告；已保存内容不会因此删除。", caution=True
                        ),
                        ft.Button("重新读取", on_click=retry, height=48),
                        ft.TextButton(
                            "返回波段机会", on_click=open_route("/", gen, actor), height=48
                        ),
                    ],
                    spacing=24,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                )
            if current(gen, actor):
                content_container.content = content
            elif gen == state["generation"] and state["connected"]:
                revoke()
                await navigate("/login")

        async def on_route_change(e):
            if e.route != state["route"]:
                await navigate(e.route, from_browser=True)

        page.on_route_change = on_route_change

        async def show_settings(e):
            if state["actor"] and state["actor"].is_valid and state["connected"]:
                state["settings_return"] = state["route"]
                await navigate("/settings")

        settings_button.on_click = show_settings
        header = ft.Row(
            [
                ft.Text("波段观察", size=18, weight=ft.FontWeight.BOLD, color=INK, expand=True),
                ft.Container(
                    ft.Text("合成演示", size=13, color=ACCENT),
                    bgcolor=BAND,
                    padding=8,
                )
                if APP_MODE == "demo"
                else ft.Container(),
                settings_button,
            ],
            spacing=8,
        )
        main_column = ft.Container(
            width=760,
            padding=ft.Padding.only(left=20, right=20, top=16, bottom=32),
            content=ft.Column(
                [header, ft.Divider(height=16), content_container],
                spacing=16,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )
        page.add(
            ft.ResponsiveRow(
                [ft.Column([main_column], horizontal_alignment=ft.CrossAxisAlignment.CENTER)]
            )
        )
        await render()
        page.update()

        async def watchdog():
            try:
                while True:
                    await asyncio.sleep(5)
                    if state["actor"] and not state["actor"].is_valid:
                        revoke()
                        await navigate("/login")
            except asyncio.CancelledError:
                pass

        watchdog_task = asyncio.create_task(watchdog())

        async def on_disconnect(e):
            nonlocal watchdog_task
            state["connected"] = False
            state["generation"] += 1
            watchdog_task.cancel()

        async def on_connect(e):
            nonlocal watchdog_task
            if state["closed"]:
                return
            state["connected"] = True
            state["generation"] += 1
            if watchdog_task.done() or watchdog_task.cancelling():
                watchdog_task = asyncio.create_task(watchdog())
            if state["actor"] and not state["actor"].is_valid:
                revoke()
            if APP_MODE == "demo" and state["actor"] is None:
                state["actor"] = create_demo_actor()
            await render()
            page.update()

        async def on_close(e):
            state["closed"] = True
            state["connected"] = False
            revoke()
            watchdog_task.cancel()

        page.on_disconnect = on_disconnect
        page.on_connect = on_connect
        page.on_close = on_close

    return main


class SecurityMiddleware:
    """ASGI middleware for host validation, origin checking, and upload prevention."""

    def __init__(self, app_instance: Any, is_demo: bool, public_base_url: str):
        self.app = app_instance
        self.is_demo = is_demo
        self.public_base_url = public_base_url
        parsed = urlparse(public_base_url)
        self.expected_scheme = (parsed.scheme or "").lower()
        self.expected_hostname = (parsed.hostname or "").lower()
        self.expected_port = parsed.port or (443 if self.expected_scheme == "https" else 80)

    def _parse_host_port(self, netloc: str) -> tuple[str, int | None] | None:
        """Parse host and optional port according to RFC 3986 / RFC 6454."""
        if not netloc:
            return None
        netloc = netloc.strip().lower()
        if netloc.startswith("["):
            end_bracket = netloc.find("]")
            if end_bracket == -1:
                return None
            hostname = netloc[1:end_bracket]
            rest = netloc[end_bracket + 1 :]
            if rest == "":
                return (hostname, None)
            if not rest.startswith(":"):
                return None
            port_str = rest[1:]
            if not port_str.isdigit():
                return None
            try:
                port = int(port_str)
                if not (1 <= port <= 65535):
                    return None
                return (hostname, port)
            except ValueError:
                return None
        else:
            parts = netloc.split(":")
            if len(parts) == 1:
                return (parts[0], None)
            elif len(parts) == 2:
                if not parts[1].isdigit():
                    return None
                try:
                    port = int(parts[1])
                    if not (1 <= port <= 65535):
                        return None
                    return (parts[0], port)
                except ValueError:
                    return None
            return None

    def _validate_host(self, host_header: str) -> bool:
        if not host_header:
            return False
        parsed = self._parse_host_port(host_header)
        if not parsed:
            return False
        hostname, port = parsed
        effective_port = (
            port if port is not None else (443 if self.expected_scheme == "https" else 80)
        )
        if self.is_demo:
            if hostname not in ("127.0.0.1", "localhost", "::1"):
                return False
            return effective_port == self.expected_port
        else:
            return hostname == self.expected_hostname and effective_port == self.expected_port

    def _validate_origin(self, origin_header: str) -> bool:
        if not origin_header:
            return False
        try:
            parsed = urlparse(origin_header.strip())
        except (ValueError, AttributeError):
            return False

        if parsed.path != "" or parsed.query or parsed.params or parsed.fragment:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False

        scheme = (parsed.scheme or "").lower()
        if scheme != self.expected_scheme:
            return False

        netloc = (parsed.netloc or "").lower()
        parsed_hp = self._parse_host_port(netloc)
        if not parsed_hp:
            return False
        hostname, port = parsed_hp
        origin_port = port if port is not None else (443 if scheme == "https" else 80)

        # RFC 6454 strict comparison against configured public_base_url
        return (
            scheme == self.expected_scheme
            and hostname == self.expected_hostname
            and origin_port == self.expected_port
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        # Retired workbench URLs are gone, not compatibility aliases.
        unavailable = path in {"/research", "/discover"} or path.startswith(
            ("/research/", "/discover/", "/company/", "/jobs/", "/upload")
        )
        if unavailable:
            if scope["type"] == "http":
                if callable(send):
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 404,
                            "headers": [(b"content-type", b"text/plain")],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"Not Found",
                        }
                    )
                return
            elif scope["type"] == "websocket":
                if callable(send):
                    await send({"type": "websocket.close", "code": 4404})
                return

        headers = dict(scope.get("headers", []))
        host_header = headers.get(b"host", b"").decode("utf-8")

        # Validate Host for both http and websocket
        if not self._validate_host(host_header):
            if scope["type"] == "http":
                if callable(send):
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 403,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b"Forbidden: Invalid Host header",
                        }
                    )
                return
            elif scope["type"] == "websocket":
                if callable(send):
                    await send({"type": "websocket.close", "code": 4403})
                return

        # Check WebSocket Origin
        if scope["type"] == "websocket":
            origin_header = headers.get(b"origin", b"").decode("utf-8")
            if not self._validate_origin(origin_header):
                if callable(send):
                    await send({"type": "websocket.close", "code": 4403})
                return

        return await self.app(scope, receive, send)


def get_asgi_app():
    import flet.fastapi as flet_fastapi

    inner_app = flet_fastapi.app(
        build_app(), assets_dir=str(Path(__file__).resolve().parents[1] / "assets")
    )

    return SecurityMiddleware(
        inner_app, is_demo=(APP_MODE == "demo"), public_base_url=PUBLIC_BASE_URL
    )


asgi_app = get_asgi_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "a_stock_tracker.app:asgi_app", host=HOST, port=PORT, reload=False, access_log=False
    )
