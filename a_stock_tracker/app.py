"""Authenticated, read-only Flet Web application for individual-stock research."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlparse

from a_stock_tracker.auth import (
    Actor,
    AuthError,
    check_production_auth_config,
    create_demo_actor,
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
from a_stock_tracker.web_assets import CHINESE_FONT, PublicAssetCompression

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

    research = DemoResearchClient() if APP_MODE == "demo" else configured_client()

    async def main(page: ft.Page):
        page.title = "个股研究助手"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.fonts = {"NotoSansSC": CHINESE_FONT}
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
            state.pop("request_feedback", None)
            if state["actor"]:
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
                else:
                    try:
                        state["actor"] = consume_owner_actor(page.auth, GITHUB_ALLOWED_USER_ID)
                    except AuthError:
                        login_message.value = "登录被拒绝：仅允许配置的所有者。"
                    else:
                        login_message.value = ""
                        await navigate("/")
                        return
                page.logout()
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
            revoke()
            await navigate("/login")

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
                feedback = paragraph("原请求受理状态尚未确认；只能核对原编号，不另起一轮收费。")
                reconcile = ft.Button("核对原请求受理状态", height=48)

                async def reconcile_pending(e):
                    if not current(gen, actor) or reconcile.disabled:
                        return
                    reconcile.disabled = True
                    page.update()
                    try:
                        accepted = await asyncio.to_thread(
                            research.submit, json.loads(saved["payload"])
                        )
                    except (ResearchQuoteChanged, ResearchRejected, ValueError) as exc:
                        if actor is state["actor"] and state["pending"] is saved:
                            state["pending"] = None
                        if current(gen, actor):
                            state["request_feedback"] = (
                                "旧报价已被拒绝；已重新获取报价，请重新核对并勾选确认。"
                                if isinstance(exc, ResearchQuoteChanged)
                                else str(exc)
                            )
                            await navigate(saved["route"])
                    except ResearchUnavailable as exc:
                        if current(gen, actor):
                            feedback.value = str(exc)
                            reconcile.disabled = False
                            page.update()
                    else:
                        if actor is state["actor"] and state["pending"] is saved:
                            state["pending"] = None
                        if current(gen, actor):
                            await navigate("/analysis/" + accepted["id"])

                reconcile.on_click = reconcile_pending
                return ft.Column(
                    [
                        heading("核对原研究请求", level=1),
                        paragraph(
                            f"请求编号：{payload['id']}\n范围：{','.join(payload['codes']) or '全市场初筛'}"
                        ),
                        paragraph(
                            f"已确认预算：{payload['quote']['requests']} 次数据请求 / {payload['quote']['seconds']} 秒"
                        ),
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
            if not resume_id:
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
            initial = query.get("mode", ["screen"])[0]
            if initial not in info["modes"]:
                initial = "screen"
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
                codes.visible = mode.value != "screen"
                continuation_note.visible = mode.value == "screen" and bool(resume_id)
                if resume_source and resume_id:
                    coverage = resume_source["result"]["coverage"]
                    continuation_note.value = (
                        f"继续原初筛 {resume_id}\n冻结依据日期 {coverage['data_date']}，"
                        f"尚未覆盖 {coverage['counts']['pending']} 家；只扫描未覆盖部分，原批次不变。\n"
                        f"本次另增最多 {selected['requests']} 次数据请求 / {selected['seconds']} 秒，不调用模型；只覆盖剩余部分，不自动重试缺口。"
                    )
                confirmation.value = False
                confirmation.label = (
                    "已核对范围、预算与模型，接受模型费用无硬上限"
                    if mode.value == "financial" and not info["demo"]
                    else "已核对范围、预算与模型，确认启动"
                )
                submit.disabled = True
                # Show only the selected mode's limits so the cost being confirmed stays scannable.
                lines = [
                    "合成演示：不调用真实接口，不产生费用。"
                    if info["demo"]
                    else "确认后后台请求行情"
                    + ("并调用模型。" if mode.value == "financial" else "，不调用模型。"),
                    f"上限：{selected['requests']} 次数据请求 · {selected['seconds']} 秒"
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
                try:
                    accepted = await asyncio.to_thread(
                        research.submit, json.loads(saved["payload"])
                    )
                except ResearchQuoteChanged:
                    if actor is state["actor"] and state["pending"] is saved:
                        state["pending"] = None
                    if current(gen, actor):
                        await reload_quote()
                except (ValueError, ResearchRejected) as exc:
                    if actor is state["actor"] and state["pending"] is saved:
                        state["pending"] = None
                    if current(gen, actor):
                        pending = None
                        mode.disabled = codes.disabled = confirmation.disabled = False
                        refresh()
                        feedback.value = str(exc)
                        page.update()
                except ResearchUnavailable as exc:
                    if current(gen, actor):
                        submit.content = "核对原请求受理状态"
                        submit.disabled = False
                        feedback.value = str(exc)
                        page.update()
                else:
                    if actor is state["actor"] and state["pending"] is saved:
                        state["pending"] = None
                    if current(gen, actor):
                        await navigate("/analysis/" + accepted["id"])

            mode.on_change = refresh
            codes.on_change = refresh
            confirmation.on_change = consent
            submit.on_click = start
            refresh()
            if state.get("request_feedback"):
                feedback.value = state.pop("request_feedback")
            controls = [
                ft.TextButton(
                    icon=ft.Icons.ARROW_BACK,
                    content="返回研究首页",
                    on_click=open_route("/", gen, actor),
                    height=48,
                ),
                heading("选股与研究", size=30, level=1),
                paragraph("选择目标 → 核对预算并启动 → 查看进度与结果。"),
                heading("这次想做什么", size=18),
                mode,
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
            return ft.Column(
                controls, spacing=20, horizontal_alignment=ft.CrossAxisAlignment.STRETCH
            )

        async def home_page(gen, actor):
            controls = [
                heading("研究首页", size=30, level=1),
                ft.Button(
                    "开始选股与研究", on_click=open_route("/analysis", gen, actor), height=48
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
                if APP_MODE == "demo":
                    from a_stock_tracker.opportunity_demo import demo_board

                    board = demo_board()
                else:
                    board = await asyncio.to_thread(load_board, actor, OPPORTUNITY_ROOT)
                controls.append(heading("已发布批次", size=22))
                controls.append(
                    live_prices(
                        board,
                        lambda value: opportunity_view(
                            value,
                            None,
                            lambda c: open_route("/opportunity/" + c, gen, actor),
                            open_route("/", gen, actor),
                            embedded=True,
                        ),
                        gen,
                        actor,
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
                elif route.startswith("/opportunity/"):
                    if APP_MODE == "demo":
                        from a_stock_tracker.opportunity_demo import demo_board

                        board = demo_board()
                    else:
                        board = await asyncio.to_thread(load_board, actor, OPPORTUNITY_ROOT)
                    content = live_prices(
                        board,
                        lambda value: opportunity_view(
                            value,
                            route.rsplit("/", 1)[-1],
                            lambda c: open_route("/opportunity/" + c, gen, actor),
                            open_route("/", gen, actor),
                        ),
                        gen,
                        actor,
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
            if APP_MODE == "demo" and state["actor"] is None:
                state["actor"] = create_demo_actor()
            await render()
            page.update()

        async def on_close(e):
            state["closed"] = True
            stop_poll()
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
