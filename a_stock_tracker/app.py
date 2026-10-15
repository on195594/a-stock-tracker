"""Authenticated, read-only Flet Web application for individual-stock research."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time as time_mod
import uuid
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlparse

from pydantic import BaseModel

from a_stock_tracker.auth import (
    AUTH_EXPIRY_SECONDS,
    Actor,
    AuthError,
    check_production_auth_config,
    create_demo_actor,
    create_exchange_ticket,
    parse_cookie_header,
    redeem_exchange_ticket,
    register_session,
    revoke_exchange_tickets,
    revoke_session,
    sign_session_token,
    verify_session_token,
)
from a_stock_tracker.oauth_completion import OwnerAuthorization, consume_owner_actor
from a_stock_tracker.opportunities import SHANGHAI, load_board
from a_stock_tracker.opportunity_view import (
    ACCENT,
    BACKGROUND,
    BAND,
    ERROR,
    INK,
    LABEL_STYLE,
    heading,
    notice,
    opportunity_view,
    paragraph,
    window_theme,
)
from a_stock_tracker.research_client import (
    DemoResearchClient,
    ResearchQuoteChanged,
    ResearchRejected,
    ResearchUnavailable,
    configured_client,
)
from a_stock_tracker.research_service import quote
from a_stock_tracker.research_view import recent_controls, result_view
from a_stock_tracker.web_assets import PublicAssetCompression

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


def budget_duration(seconds: int) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes} 分钟" + (f" {remainder} 秒" if remainder else "")


TRANSPARENT_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00"
    b"\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc`\x00\x02\x00\x00\x05\x00"
    b"\x01z^\xab?\x00\x00\x00\x00IEND\xaeB`\x82"
)


def build_app():
    import flet as ft

    research = DemoResearchClient() if APP_MODE == "demo" else configured_client()

    # Remove this adapter together with app.py in refactor phase 4.
    class ResearchAdapter:
        def __init__(self, client):
            self.client = client

        @staticmethod
        def plain(value):
            if isinstance(value, BaseModel):
                return value.model_dump(by_alias=True, exclude_unset=True)
            if isinstance(value, list) and any(isinstance(item, BaseModel) for item in value):
                return [ResearchAdapter.plain(item) for item in value]
            return value

        def info(self):
            return self.plain(self.client.info())

        def recent(self):
            return self.plain(self.client.recent())

        def submit(self, payload):
            return self.plain(self.client.submit(payload))

        def status(self, identifier):
            return self.plain(self.client.status(identifier))

    if research is not None:
        research = ResearchAdapter(research)

    async def main(page: ft.Page):
        def get_initial_actor(page: ft.Page) -> Actor | None:
            if APP_MODE == "demo":
                return create_demo_actor()
            conn = getattr(getattr(page, "session", None), "connection", None)
            ws = getattr(conn, "_FletApp__websocket", None)
            if ws is not None:
                scope_actor = getattr(ws, "scope", {}).get("actor")
                if isinstance(scope_actor, Actor) and scope_actor.is_valid:
                    return scope_actor
                token = getattr(ws, "cookies", {}).get("stock_session")
                return verify_session_token(
                    token, GITHUB_CLIENT_SECRET or "", GITHUB_ALLOWED_USER_ID
                )
            return None

        session_beacon = ft.Image(src="", width=1, height=1, opacity=0)
        page.title = "个股研究助手"
        page.theme_mode = ft.ThemeMode.LIGHT
        # The initial remote update replaces Flutter's default Roboto theme. Do not
        # render Chinese in that old font during AnimatedTheme's transition.
        page.theme_animation_style = ft.AnimationStyle.no_animation()
        page.theme = window_theme()
        page.bgcolor = BACKGROUND
        page.padding = 0
        page.scroll = ft.ScrollMode.AUTO
        state: dict[str, Any] = {
            "route": page.route or "/",
            "actor": get_initial_actor(page),
            "generation": 0,
            "connected": True,
            "closed": False,
            "revoked": False,
            "settings_return": "/",
            "poll": None,
            "pending": None,
            "expiry_tasks": set(),
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

        def stop_expiry():
            for task in state["expiry_tasks"]:
                task.cancel()
            state["expiry_tasks"].clear()

        def live_prices(data, renderer, gen, actor):
            """Expire a read-only snapshot locally; never poll a completed batch."""
            rows = []

            def collect(value):
                if isinstance(value, dict):
                    if value.get("status") in {"reference", "observation"} and "expires" in value:
                        rows.append(value)
                    for child in value.values():
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)

            collect(data)
            if not rows:
                return renderer(data)
            snapshot = deepcopy(data)
            rows = []
            collect(snapshot)
            deadlines = [
                (
                    row,
                    datetime.combine(
                        date.fromisoformat(row["expires"]) + timedelta(days=1), time(), SHANGHAI
                    ),
                )
                for row in rows
                if not row.get("expired")
            ]

            def expire():
                now = datetime.now(SHANGHAI)
                for row, deadline in deadlines:
                    if now >= deadline:
                        row["expired"] = True
                return [deadline for row, deadline in deadlines if not row.get("expired")]

            remaining = expire()
            container = ft.Container(content=renderer(snapshot))

            async def watch_expiry():
                try:
                    pending_dates = remaining
                    while pending_dates and current(gen, actor):
                        await asyncio.sleep(
                            max(0, (min(pending_dates) - datetime.now(SHANGHAI)).total_seconds())
                        )
                        if not current(gen, actor):
                            return
                        pending_dates = expire()
                        container.content = renderer(snapshot)
                        page.update()
                except asyncio.CancelledError:
                    pass

            if remaining and current(gen, actor):
                task = asyncio.create_task(watch_expiry())
                state["expiry_tasks"].add(task)
                task.add_done_callback(state["expiry_tasks"].discard)
            return container

        def revoke(*, clear_auth: bool = True):
            if clear_auth:
                page.logout()
            stop_expiry()
            state["pending"] = None
            state["revoked"] = True
            state.pop("request_feedback", None)
            if state["actor"]:
                if state["actor"].sid:
                    revoke_session(state["actor"].sid)
                state["actor"].revoke()
            state["actor"] = None
            state["generation"] += 1

        def stop_poll():
            if state["poll"] is not None:
                state["poll"].cancel()
                state["poll"] = None

        async def navigate(route: str, *, from_browser: bool = False):
            stop_poll()
            stop_expiry()
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
                revoke(clear_auth=False)
                if e.error:
                    login_message.value = "登录失败，请重新授权。"
                    if GITHUB_ALLOWED_USER_ID:
                        revoke_exchange_tickets(GITHUB_ALLOWED_USER_ID)
                else:
                    try:
                        state["actor"] = consume_owner_actor(page.auth, GITHUB_ALLOWED_USER_ID)
                        state["revoked"] = False
                    except AuthError:
                        login_message.value = "登录被拒绝：仅允许配置的所有者。"
                        if GITHUB_ALLOWED_USER_ID:
                            revoke_exchange_tickets(GITHUB_ALLOWED_USER_ID)
                    else:
                        login_message.value = ""
                        if APP_MODE == "production" and GITHUB_CLIENT_SECRET:
                            sid = register_session(
                                state["actor"].user_id, time_mod.time() + AUTH_EXPIRY_SECONDS
                            )
                            state["actor"].sid = sid
                            ticket = create_exchange_ticket(state["actor"].user_id, sid)
                            session_beacon.src = f"{PUBLIC_BASE_URL}/api/session?ticket={ticket}"
                            page.update()
                        await navigate("/")
                        return
                page.logout()
                if GITHUB_ALLOWED_USER_ID:
                    revoke_exchange_tickets(GITHUB_ALLOWED_USER_ID)
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
                    authorization=OwnerAuthorization,
                )

        async def logout(e):
            if not state["connected"]:
                return
            sid_to_clear = state["actor"].sid if state["actor"] else ""
            revoke()
            if APP_MODE == "production":
                param = f"clear={sid_to_clear}" if sid_to_clear else "clear=1"
                session_beacon.src = f"{PUBLIC_BASE_URL}/api/session?{param}"
                page.update()
            await navigate("/login")

        async def submit_saved(saved, actor):
            """Submit the frozen request; only definitive outcomes clear its recovery state."""
            try:
                accepted = await asyncio.to_thread(research.submit, json.loads(saved["payload"]))
            except (ResearchQuoteChanged, ResearchRejected, ValueError) as exc:
                outcome, value = "rejected", exc
            except ResearchUnavailable as exc:
                return "unknown", exc
            else:
                outcome, value = "accepted", accepted
            if actor is state["actor"] and state["pending"] is saved:
                state["pending"] = None
            return outcome, value

        async def research_page(gen: int, actor: Actor, route: str):
            back = open_route("/analysis", gen, actor)
            if research is None:
                return ft.Column(
                    [
                        heading("选股与研究", size=30, level=1),
                        notice(
                            "研究服务尚未连接",
                            "管理员需一次配置独立研究服务。配置完成后在网页输入、确认、查看结果，用户无需终端命令。",
                            caution=True,
                        ),
                        ft.TextButton(
                            icon=ft.Icons.ARROW_BACK,
                            content="返回研究首页",
                            on_click=open_route("/", gen, actor),
                            height=48,
                        ),
                    ],
                    spacing=24,
                )
            if route.startswith("/analysis/"):
                parts = route.strip("/").split("/")
                identifier = parts[1] if len(parts) >= 2 else ""
                code = parts[2] if len(parts) == 3 else None
                if (
                    not re.fullmatch(r"[a-f0-9]{32}", identifier)
                    or len(parts) > 3
                    or (code and not re.fullmatch(r"(?:60\d{4}\.SH|00\d{4}\.SZ)", code))
                ):
                    return ft.Column(
                        [
                            heading("请求编号不正确", size=30, level=1),
                            paragraph("请核对请求编号，或返回重新查看最近研究。"),
                            ft.TextButton(
                                icon=ft.Icons.ARROW_BACK,
                                content="返回选股与研究",
                                on_click=back,
                                height=48,
                            ),
                        ],
                        spacing=24,
                    )
                try:
                    run = await asyncio.to_thread(research.status, identifier)
                except ResearchUnavailable as exc:
                    return ft.Column(
                        [
                            heading("研究状态暂不可确认", level=1),
                            paragraph(str(exc)),
                            ft.Button(
                                "重新查看原请求",
                                on_click=open_route(state["route"], gen, actor),
                                height=48,
                            ),
                            ft.TextButton(
                                icon=ft.Icons.ARROW_BACK,
                                content="返回选股与研究",
                                on_click=back,
                                height=48,
                            ),
                        ],
                        spacing=24,
                    )

                async def poll():
                    last_run = run
                    consecutive_errors = 0
                    try:
                        while current(gen, actor):
                            await asyncio.sleep(2)
                            try:
                                updated = await asyncio.to_thread(research.status, identifier)
                                consecutive_errors = 0
                            except ResearchUnavailable:
                                consecutive_errors += 1
                                if consecutive_errors < 3 and current(gen, actor):
                                    continue
                                raise
                            if not current(gen, actor):
                                return
                            if updated == last_run:
                                continue
                            last_run = updated
                            stop_expiry()
                            content_container.content = live_prices(
                                updated,
                                lambda value: result_view(
                                    value,
                                    lambda r: open_route(r, gen, actor),
                                    back,
                                    code,
                                    lambda: current(gen, actor),
                                ),
                                gen,
                                actor,
                            )
                            if state["connected"]:
                                try:
                                    page.update()
                                except Exception:
                                    pass
                            if updated["status"] != "running":
                                return
                    except asyncio.CancelledError:
                        pass
                    except ResearchUnavailable:
                        if current(gen, actor):
                            content_container.content = ft.Column(
                                [
                                    heading("研究状态暂不可确认", level=1),
                                    paragraph(
                                        "任务可能仍在后台执行。重新查看原请求，不另起一轮收费。"
                                    ),
                                    ft.Button(
                                        "重新查看原请求",
                                        on_click=open_route(state["route"], gen, actor),
                                        height=48,
                                    ),
                                    ft.TextButton(
                                        icon=ft.Icons.ARROW_BACK,
                                        content="返回选股与研究",
                                        on_click=back,
                                        height=48,
                                    ),
                                ]
                            )
                            if state["connected"]:
                                try:
                                    page.update()
                                except Exception:
                                    pass

                if current(gen, actor) and run["status"] == "running":
                    stop_poll()
                    state["poll"] = asyncio.create_task(poll())
                return live_prices(
                    run,
                    lambda value: result_view(
                        value,
                        lambda r: open_route(r, gen, actor),
                        back,
                        code,
                        lambda: current(gen, actor),
                    ),
                    gen,
                    actor,
                )
            if state["pending"] is not None:
                saved = state["pending"]
                payload = json.loads(saved["payload"])
                feedback = paragraph("原请求受理状态尚未确认。")
                frozen_quote = payload["quote"]
                frozen_model = frozen_quote.get("model")
                recovery_details: list[ft.Control] = [
                    paragraph(f"请求编号：{payload['id']}"),
                    paragraph(f"预算原值：{frozen_quote['seconds']} 秒"),
                ]
                recovery_cost: list[ft.Control] = [
                    paragraph(f"范围：{','.join(payload['codes']) or '全市场初筛'}"),
                    paragraph(
                        f"已确认预算：最多 {frozen_quote['requests']} 次数据请求 / "
                        f"{budget_duration(frozen_quote['seconds'])}"
                    ),
                ]
                if frozen_model:
                    model_budget = frozen_quote["model_budget"]
                    recovery_cost.extend(
                        [
                            paragraph(
                                f"已确认模型：{frozen_model['provider']} / {frozen_model['model']}"
                            ),
                            paragraph(
                                f"最多 {model_budget['calls']} 次调用，每次本地等待 ≤"
                                f"{model_budget['local_timeout_seconds']} 秒。"
                                "输出 token 与费用无硬上限；本地超时不保证提供方停止计费，失败调用也可能计费。"
                            ),
                        ]
                    )
                else:
                    recovery_cost.append(paragraph("不调用模型。"))
                reconcile = ft.Button("继续确认原请求", height=48)

                async def reconcile_pending(e):
                    if not current(gen, actor) or reconcile.disabled:
                        return
                    reconcile.disabled = True
                    page.update()
                    outcome, value = await submit_saved(saved, actor)
                    if not current(gen, actor):
                        return
                    if outcome == "rejected":
                        state["request_feedback"] = (
                            "旧报价已被拒绝；已重新获取报价，请重新核对并勾选确认。"
                            if isinstance(value, ResearchQuoteChanged)
                            else str(value)
                        )
                        await navigate(saved["route"])
                    elif outcome == "unknown":
                        feedback.value = str(value)
                        reconcile.disabled = False
                        page.update()
                    else:
                        await navigate("/analysis/" + value["id"])

                reconcile.on_click = reconcile_pending
                return ft.Column(
                    [
                        heading("继续确认原研究请求", level=1),
                        notice(
                            "核对也可能启动原请求",
                            "将按同一编号重新提交已确认的范围与预算。如果服务此前没有收到请求，此操作会启动研究并可能产生费用；已受理则返回原结果，不创建另一轮请求。",
                            caution=True,
                            extra=recovery_cost,
                        ),
                        ft.ExpansionTile(title=paragraph("请求详情"), controls=recovery_details),
                        feedback,
                        reconcile,
                        ft.TextButton(
                            "返回研究首页", on_click=open_route("/", gen, actor), height=48
                        ),
                    ],
                    spacing=20,
                )
            try:
                info, recent = await asyncio.gather(
                    asyncio.to_thread(research.info), asyncio.to_thread(research.recent)
                )
            except ResearchUnavailable:
                return ft.Column(
                    [
                        heading("选股与研究", size=30, level=1),
                        notice(
                            "研究服务暂时无法连接",
                            "后台研究服务未运行或无法连接。请联系管理员核对宿主服务状态；已受理的后台研究继续受预算约束。",
                            caution=True,
                        ),
                        ft.Button(
                            "重新尝试连接",
                            on_click=open_route(state["route"], gen, actor),
                            height=48,
                        ),
                        ft.TextButton(
                            icon=ft.Icons.ARROW_BACK,
                            content="返回研究首页",
                            on_click=open_route("/", gen, actor),
                            height=48,
                        ),
                    ],
                    spacing=24,
                )
            query = parse_qs(urlparse(state["route"]).query)
            resume_id = query.get("resume_from", [""])[0]
            if not resume_id and query.get("mode", ["financial"])[0] == "screen":
                latest_screen = next((r for r in recent if r["request"]["mode"] == "screen"), None)
                if latest_screen and latest_screen["status"] == "partial":
                    resume_id = latest_screen["id"]
            resume_source = None
            if resume_id:
                try:
                    resume_source = await asyncio.to_thread(research.status, resume_id)
                    pending_count = (
                        resume_source.get("result", {})
                        .get("coverage", {})
                        .get("counts", {})
                        .get("pending", 0)
                    )
                    if (
                        resume_source["request"]["mode"] != "screen"
                        or resume_source["status"] != "partial"
                        or not pending_count
                        or not resume_source.get("can_continue", False)
                    ):
                        if query.get("resume_from"):
                            return ft.Column(
                                [
                                    notice(
                                        "该初筛不能继续",
                                        "已无待覆盖股票、冻结资料已超过七天或筛选规则已变化。请返回入口核对并确认新的初筛。",
                                        caution=True,
                                    ),
                                    ft.TextButton(
                                        icon=ft.Icons.ARROW_BACK,
                                        content="返回选股与研究",
                                        on_click=open_route("/analysis", gen, actor),
                                        height=48,
                                    ),
                                ]
                            )
                        resume_id = ""
                        resume_source = None
                except ResearchUnavailable:
                    return ft.Column(
                        [
                            notice(
                                "无法核对原初筛",
                                "请返回最近研究核对原记录；未启动新的收费请求。",
                                caution=True,
                            ),
                            ft.TextButton(
                                icon=ft.Icons.ARROW_BACK,
                                content="返回选股与研究",
                                on_click=open_route("/analysis", gen, actor),
                                height=48,
                            ),
                        ]
                    )
                if not current(gen, actor):
                    return ft.Column([])
            initial = query.get("mode", ["financial"])[0]
            if initial not in info["modes"]:
                initial = "financial"
            mode = ft.RadioGroup(
                value=initial,
                content=ft.Column(
                    [
                        ft.Radio(value=k, label=v["title"], label_style=LABEL_STYLE, height=48)
                        for k, v in info["modes"].items()
                    ],
                    spacing=4,
                ),
            )
            codes = ft.TextField(
                label="股票代码",
                value=query.get("code", [""])[0],
                hint_text="600519 或 600519.SH；多只用逗号分隔",
                max_length=80,
                multiline=True,
                min_lines=1,
                max_lines=3,
            )
            task_heading = ft.Container()
            budget = paragraph("")
            continuation_note = paragraph("")
            confirmation = ft.Checkbox(
                label="已核对范围、预算与模型，确认启动", label_style=LABEL_STYLE, value=False
            )
            feedback = paragraph("")
            submit = ft.Button("确认启动研究", height=48, disabled=True)
            request_id = uuid.uuid4().hex
            pending = None
            quote_stale = False

            def current_quote():
                if mode.value == "screen" and resume_source and resume_id:
                    return resume_source["continuation_quote"]
                return quote(info, mode.value)

            async def reload_quote():
                nonlocal info, resume_source, quote_stale, pending
                pending = None
                quote_stale = True
                confirmation.value = False
                confirmation.disabled = True
                try:
                    new_info = await asyncio.to_thread(research.info)
                    new_source = (
                        await asyncio.to_thread(research.status, resume_id) if resume_id else None
                    )
                except ResearchUnavailable:
                    if current(gen, actor):
                        submit.content = "重新获取报价"
                        submit.disabled = False
                        feedback.value = (
                            "旧报价已被拒绝；暂时无法获取新报价。输入已保留，请重新获取报价后确认。"
                        )
                        page.update()
                    return
                if not current(gen, actor):
                    return
                info, resume_source = new_info, new_source
                if (
                    mode.value == "screen"
                    and resume_source
                    and not resume_source.get("can_continue")
                ):
                    feedback.value = "该检查点已继续或已不可续跑，请返回来源初筛查看最新进度。"
                    submit.disabled = True
                    page.update()
                    return
                quote_stale = False
                mode.disabled = codes.disabled = confirmation.disabled = False
                refresh()
                feedback.value = "报价或模型已变化，已刷新报价并保留输入；请重新核对并勾选确认。"
                page.update()

            def refresh(e=None):
                if not current(gen, actor):
                    return
                nonlocal pending, request_id
                pending = None
                request_id = uuid.uuid4().hex
                submit.content = (
                    "确认新增预算并继续初筛"
                    if mode.value == "screen" and resume_id
                    else "确认启动研究"
                )
                selected = {**current_quote(), "available": info["modes"][mode.value]["available"]}
                task_heading.content = heading(
                    {
                        "financial": "研究一家公司",
                        "screen": "从初筛找公司",
                        "swing": "核对价格参考",
                    }[mode.value],
                    size=30,
                    level=1,
                )
                codes.visible = mode.value != "screen"
                codes.label = (
                    "一家公司的股票代码" if mode.value == "financial" else "股票代码（1–5 家）"
                )
                codes.hint_text = (
                    "例如 600519 或 600519.SH"
                    if mode.value == "financial"
                    else "例如 600519, 600900；最多 5 家"
                )
                codes.multiline = mode.value == "swing"
                codes.max_lines = 3 if mode.value == "swing" else 1
                continuation_note.visible = mode.value == "screen" and bool(resume_id)
                if resume_source and resume_id:
                    coverage = resume_source["result"]["coverage"]
                    continuation_note.value = (
                        f"继续原初筛\n冻结依据日期 {coverage['data_date']}，"
                        f"尚未覆盖 {coverage['counts']['pending']} 家；只扫描未覆盖部分，原批次不变。\n"
                        "只覆盖剩余部分，不自动重试缺口；以上上限是本次新增预算。"
                    )
                confirmation.value = False
                confirmation.label = (
                    "已核对范围、预算与模型，接受模型费用无硬上限"
                    if mode.value == "financial" and not info["demo"]
                    else (
                        "已核对范围、预算与模型，确认启动"
                        if mode.value == "financial"
                        else "已核对范围与预算，确认启动（不调用模型）"
                    )
                )
                submit.disabled = True
                # Show only the selected mode's limits so the cost being confirmed stays scannable.
                lines = [
                    "合成演示：不调用真实接口，不产生费用。"
                    if info["demo"]
                    else "确认后后台请求行情"
                    + ("并调用模型。" if mode.value == "financial" else "，不调用模型。"),
                    f"上限：{selected['requests']} 次数据请求 · {budget_duration(selected['seconds'])}"
                    + (f" · {selected['reports']} 份财报报告" if selected["reports"] else ""),
                ]
                if mode.value == "screen":
                    screening = selected.get("prefilter")
                    if screening:
                        lines.append(
                            f"初筛门槛：0＜PB≤{screening['pb_max']}；"
                            f"总市值≥{screening['total_mv_min_yi']}亿元，不设上限。"
                        )
                    lines.append(
                        "结果是筛后同业排名（不是完整行业前三），只代表财报资格、不代表投资价值；"
                        "门槛未经收益验证，可能漏选。"
                    )
                elif mode.value == "financial":
                    model_budget = quote(info, mode.value)["model_budget"]
                    lines.append(
                        f"模型：{info['model']['provider']} / {info['model']['model']}，"
                        f"最多 {model_budget['calls']} 次调用，每次本地等待 ≤"
                        f"{model_budget['local_timeout_seconds']} 秒。"
                    )
                    lines.append(
                        ("真实运行时：" if info["demo"] else "")
                        + "输出 token 与费用无硬上限；本地超时不保证提供方停止计费，失败调用也可能计费。"
                    )
                if mode.value == "swing":
                    lines.append(
                        "最多五家，仅计算价格纪律参考，不调用模型；不因趋势或距离排除资料完整的公司。"
                    )
                budget.value = "\n".join(lines)
                if not selected["available"]:
                    feedback.value = "本类型尚未配置，需管理员完成一次连接；不能假装已研究。"
                else:
                    feedback.value = ""
                if e is not None:
                    page.update()

            def consent(e):
                if current(gen, actor):
                    submit.disabled = not (
                        confirmation.value and info["modes"][mode.value]["available"]
                    )
                    page.update()

            async def start(e):
                nonlocal pending
                if not current(gen, actor) or submit.disabled:
                    return
                if quote_stale:
                    await reload_quote()
                    return
                if pending is None:
                    raw = [
                        c.strip().upper()
                        for c in re.split(r"[,，\s]+", codes.value or "")
                        if c.strip()
                    ]
                    normalized = [
                        c + (".SH" if c.startswith("60") else ".SZ")
                        if re.fullmatch(r"\d{6}", c)
                        else c
                        for c in raw
                    ]
                    if mode.value != "screen" and (
                        not normalized or len(normalized) > (1 if mode.value == "financial" else 5)
                    ):
                        confirmation.value = False
                        submit.disabled = True
                        feedback.value = (
                            "请只输入一家公司的股票代码，再重新确认。"
                            if mode.value == "financial"
                            else "请输入 1–5 家公司的股票代码，再重新确认。"
                        )
                        page.update()
                        return
                    pending = {
                        "id": request_id,
                        "mode": mode.value,
                        "codes": [] if mode.value == "screen" else normalized,
                        "confirmed": True,
                        "quote": current_quote(),
                    }
                if mode.value == "screen" and resume_id and pending is not None:
                    pending["resume_from"] = resume_id
                if mode.value != "screen" and query.get("from") and pending is not None:
                    pending["from"] = query["from"][0]
                saved = {
                    "payload": json.dumps(pending),
                    "route": "/analysis?"
                    + urlencode(
                        {
                            **{key: value[0] for key, value in query.items()},
                            "mode": mode.value,
                            "code": codes.value or "",
                        }
                    ),
                }
                state["pending"] = saved
                submit.disabled = True
                mode.disabled = codes.disabled = confirmation.disabled = True
                feedback.value = "正在核对受理状态，请勿重复启动。"
                page.update()
                outcome, value = await submit_saved(saved, actor)
                if not current(gen, actor):
                    return
                if outcome == "rejected":
                    if isinstance(value, ResearchQuoteChanged):
                        await reload_quote()
                    else:
                        pending = None
                        mode.disabled = codes.disabled = confirmation.disabled = False
                        refresh()
                        feedback.value = str(value)
                        page.update()
                elif outcome == "unknown":
                    # Always show the same frozen scope and fee warning before resubmitting.
                    await navigate(saved["route"])
                else:
                    await navigate("/analysis/" + value["id"])

            mode.on_change = refresh
            codes.on_change = refresh
            confirmation.on_change = consent
            submit.on_click = start
            refresh()
            if state.get("request_feedback"):
                feedback.value = state.pop("request_feedback")
            controls: list[ft.Control] = [
                ft.TextButton(
                    icon=ft.Icons.ARROW_BACK,
                    content="返回研究首页",
                    on_click=open_route("/", gen, actor),
                    height=48,
                ),
                task_heading,
                ft.ExpansionTile(title=paragraph("更换研究类型"), controls=[mode]),
                codes,
                notice(
                    "边界与成本",
                    "打开页面不产生费用，确认启动后才执行。",
                    extra=[budget, continuation_note],
                ),
                confirmation,
                submit,
                feedback,
            ]
            return ft.Column(
                controls, spacing=20, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
            )

        async def home_page(gen, actor):
            controls = [
                heading("研究首页", size=30, level=1),
                ft.Button(
                    "研究一家公司",
                    on_click=open_route("/analysis?mode=financial", gen, actor),
                    height=48,
                ),
                ft.TextButton(
                    "从初筛找公司",
                    on_click=open_route("/analysis?mode=screen", gen, actor),
                    height=48,
                ),
                ft.TextButton(
                    "独立价格参考（不调用模型）",
                    on_click=open_route("/analysis?mode=swing", gen, actor),
                    height=48,
                ),
            ]
            if research is None:
                controls.append(
                    notice(
                        "研究服务尚未连接",
                        "管理员需一次配置独立研究服务；可进入选股与研究查看连接说明。",
                        caution=True,
                    )
                )
            else:
                try:
                    info, recent = await asyncio.gather(
                        asyncio.to_thread(research.info), asyncio.to_thread(research.recent)
                    )
                    if not current(gen, actor):
                        return ft.Column([])
                    if not any(m["available"] for m in info["modes"].values()):
                        controls.append(
                            notice(
                                "研究服务尚未配置",
                                "管理员需连接行情或模型服务，完成后再确认研究。",
                                caution=True,
                            )
                        )
                    controls.append(
                        live_prices(
                            recent,
                            lambda value: ft.Column(
                                recent_controls(value, lambda r: open_route(r, gen, actor))
                            ),
                            gen,
                            actor,
                        )
                    )
                except ResearchUnavailable:
                    controls.append(
                        notice(
                            "研究服务暂时无法连接",
                            "请稍后刷新，或进入选股与研究查看连接说明；未启动新请求。",
                            caution=True,
                        )
                    )
                controls.append(
                    ft.TextButton("刷新研究进度", on_click=open_route("/", gen, actor), height=48)
                )
            if OPPORTUNITY_ROOT is not None or APP_MODE == "demo":
                controls.append(
                    ft.TextButton(
                        "历史发布批次", on_click=open_route("/published", gen, actor), height=48
                    )
                )
            return ft.Column(
                controls, spacing=20, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
            )

        async def render():
            stop_expiry()
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
                        paragraph("登录后可在网页发起选股与研究；确认范围与成本后由独立后台执行。"),
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
                if route == "/":
                    content = await home_page(gen, actor)
                elif route == "/published" or route.startswith("/opportunity/"):
                    if APP_MODE == "demo":
                        from a_stock_tracker.opportunity_demo import demo_board

                        board = demo_board()
                    else:
                        board = await asyncio.to_thread(load_board, actor, OPPORTUNITY_ROOT)
                    content = live_prices(
                        board,
                        lambda value: opportunity_view(
                            value,
                            None if route == "/published" else route.rsplit("/", 1)[-1],
                            lambda c: open_route("/opportunity/" + c, gen, actor),
                            open_route("/" if route == "/published" else "/published", gen, actor),
                            recheck_route=lambda r: open_route(r, gen, actor),
                        ),
                        gen,
                        actor,
                    )
                    if route == "/published":
                        content = ft.Column(
                            [
                                ft.TextButton(
                                    "返回研究首页", on_click=open_route("/", gen, actor), height=48
                                ),
                                content,
                            ],
                            spacing=20,
                        )
                elif route == "/analysis" or route.startswith("/analysis/"):
                    content = await research_page(gen, actor, route)
                elif route == "/settings":

                    async def logout_current(e):
                        if current(gen, actor):
                            await logout(e)

                    content = ft.Column(
                        [
                            ft.TextButton(
                                icon=ft.Icons.ARROW_BACK,
                                content="返回上一页",
                                on_click=open_route(state["settings_return"], gen, actor),
                                height=48,
                            ),
                            heading("账户与运行信息", size=30, level=1),
                            notice("当前会话", f"当前模式: {APP_MODE}\n用户身份: {actor.user_id}"),
                            paragraph(
                                "网页提交本人确认的研究请求，独立后台执行；退出后隐藏会话内容，已受理研究继续受预算约束。"
                            ),
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
                                icon=ft.Icons.ARROW_BACK,
                                content="返回研究首页",
                                on_click=open_route("/", gen, actor),
                                height=48,
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
                            icon=ft.Icons.ARROW_BACK,
                            content="返回研究首页",
                            on_click=open_route("/", gen, actor),
                            height=48,
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
                if urlparse(state["route"]).path != "/settings":
                    state["settings_return"] = state["route"]
                await navigate("/settings")

        settings_button.on_click = show_settings
        header = ft.Row(
            [
                ft.Text("个股研究助手", size=18, weight=ft.FontWeight.BOLD, color=INK, expand=True),
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
                [header, ft.Divider(height=16), content_container, session_beacon],
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
                ticks = 0
                while True:
                    await asyncio.sleep(5)
                    ticks += 1
                    if state["actor"] and not state["actor"].is_valid:
                        revoke()
                        await navigate("/login")
                    elif state["connected"] and ticks % 5 == 0:
                        try:
                            page.update()
                        except Exception:
                            pass
            except asyncio.CancelledError:
                pass

        watchdog_task = asyncio.create_task(watchdog())

        async def on_disconnect(e):
            nonlocal watchdog_task
            state["connected"] = False
            stop_poll()
            stop_expiry()
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
            if state["actor"] is None and not state["revoked"]:
                state["actor"] = get_initial_actor(page)
            await render()
            page.update()

        async def on_close(e):
            state["closed"] = True
            stop_poll()
            state["connected"] = False
            stop_expiry()
            state["pending"] = None
            state.pop("request_feedback", None)
            if state["actor"]:
                state["actor"].revoke()
                state["actor"] = None
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
            if len(parts) == 2:
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

    def _is_same_origin_http(self, headers: dict[bytes, bytes]) -> bool:
        sec_fetch_site = headers.get(b"sec-fetch-site", b"").decode("utf-8").lower().strip()
        if sec_fetch_site and sec_fetch_site != "same-origin":
            return False
        origin = headers.get(b"origin", b"").decode("utf-8").strip()
        if origin:
            return self._validate_origin(origin)
        referer = headers.get(b"referer", b"").decode("utf-8").strip()
        if referer:
            try:
                parsed = urlparse(referer)
                parsed_hp = self._parse_host_port(parsed.netloc.lower())
                if not parsed_hp:
                    return False
                hostname, port = parsed_hp
                origin_port = (
                    port if port is not None else (443 if parsed.scheme == "https" else 80)
                )
                return (
                    parsed.scheme.lower() == self.expected_scheme
                    and hostname == self.expected_hostname
                    and origin_port == self.expected_port
                )
            except Exception:
                return False
        return sec_fetch_site == "same-origin"

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
            cookie_header = headers.get(b"cookie", b"").decode("utf-8")
            cookies = parse_cookie_header(cookie_header)
            session_cookie = cookies.get("stock_session")
            if session_cookie and not self.is_demo:
                actor = verify_session_token(
                    session_cookie, GITHUB_CLIENT_SECRET or "", GITHUB_ALLOWED_USER_ID
                )
                if actor:
                    scope["actor"] = actor

        if scope["type"] == "http":
            if path == "/api/session":
                query_string = scope.get("query_string", b"").decode("utf-8")
                params = parse_qs(query_string)
                resp_headers: list[tuple[bytes, bytes]] = [
                    (b"content-type", b"image/png"),
                    (b"cache-control", b"no-store, no-cache, must-revalidate"),
                ]
                cookie_header = headers.get(b"cookie", b"").decode("utf-8")
                cookies = parse_cookie_header(cookie_header)
                if "clear" in params:
                    if self._is_same_origin_http(headers):
                        clear_val = params["clear"][0]
                        token = cookies.get("stock_session")
                        cookie_sid = None
                        if token and not self.is_demo:
                            actor = verify_session_token(
                                token, GITHUB_CLIENT_SECRET or "", GITHUB_ALLOWED_USER_ID
                            )
                            if actor:
                                cookie_sid = actor.sid
                        if clear_val and clear_val != "1":
                            revoke_session(clear_val)
                        elif cookie_sid:
                            revoke_session(cookie_sid)
                        if (
                            not cookie_sid
                            or not clear_val
                            or clear_val == "1"
                            or cookie_sid == clear_val
                        ):
                            cookie_val = (
                                b"stock_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
                                + (b"; Secure" if self.expected_scheme == "https" else b"")
                            )
                            resp_headers.append((b"set-cookie", cookie_val))
                elif "ticket" in params and not self.is_demo:
                    if self._is_same_origin_http(headers):
                        redeemed = redeem_exchange_ticket(params["ticket"][0])
                        if redeemed:
                            user_id, sid = redeemed
                            from a_stock_tracker import auth as auth_mod

                            active_entry = auth_mod.ACTIVE_SESSIONS.get(sid)
                            if (
                                active_entry
                                and active_entry[0] == user_id
                                and active_entry[1] > time_mod.time()
                            ):
                                expires_at = active_entry[1]
                                old_token = cookies.get("stock_session")
                                if old_token:
                                    revoke_session(old_token)
                                token = sign_session_token(
                                    user_id,
                                    GITHUB_CLIENT_SECRET or "",
                                    expires_at,
                                    sid=sid,
                                )
                                remaining = max(0, int(expires_at - time_mod.time()))
                                cookie_val = (
                                    f"stock_session={token}; Path=/; Max-Age={remaining}; "
                                    f"HttpOnly; SameSite=Lax"
                                    + ("; Secure" if self.expected_scheme == "https" else "")
                                ).encode("utf-8")
                                resp_headers.append((b"set-cookie", cookie_val))
                elif "token" in params and not self.is_demo:
                    if self._is_same_origin_http(headers):
                        token = params["token"][0]
                        actor = verify_session_token(
                            token, GITHUB_CLIENT_SECRET or "", GITHUB_ALLOWED_USER_ID
                        )
                        if actor:
                            cookie_val = (
                                f"stock_session={token}; Path=/; Max-Age={AUTH_EXPIRY_SECONDS}; "
                                f"HttpOnly; SameSite=Lax"
                                + ("; Secure" if self.expected_scheme == "https" else "")
                            ).encode("utf-8")
                            resp_headers.append((b"set-cookie", cookie_val))
                if callable(send):
                    await send(
                        {"type": "http.response.start", "status": 200, "headers": resp_headers}
                    )
                    await send({"type": "http.response.body", "body": TRANSPARENT_PNG})
                return

            if path == "/logout":
                if not self._is_same_origin_http(headers):
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
                                "body": b"Forbidden: Cross-origin logout",
                            }
                        )
                    return
                cookie_header = headers.get(b"cookie", b"").decode("utf-8")
                cookies = parse_cookie_header(cookie_header)
                token = cookies.get("stock_session")
                if token and not self.is_demo:
                    actor = verify_session_token(
                        token, GITHUB_CLIENT_SECRET or "", GITHUB_ALLOWED_USER_ID
                    )
                    if actor:
                        revoke_session(token)
                        revoke_exchange_tickets(actor.user_id)
                cookie_val = b"stock_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" + (
                    b"; Secure" if self.expected_scheme == "https" else b""
                )
                if callable(send):
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 303,
                            "headers": [(b"location", b"/login"), (b"set-cookie", cookie_val)],
                        }
                    )
                    await send({"type": "http.response.body", "body": b""})
                return

        return await self.app(scope, receive, send)


def get_asgi_app():
    import flet.fastapi as flet_fastapi

    inner_app = flet_fastapi.app(
        build_app(),
        assets_dir=str(Path(__file__).resolve().parents[1] / "assets"),
        session_timeout_seconds=FLET_SESSION_TIMEOUT,
    )

    return SecurityMiddleware(
        PublicAssetCompression(inner_app),
        is_demo=(APP_MODE == "demo"),
        public_base_url=PUBLIC_BASE_URL,
    )


asgi_app = get_asgi_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "a_stock_tracker.app:asgi_app", host=HOST, port=PORT, reload=False, access_log=False
    )
