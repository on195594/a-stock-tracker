"""Flet Web application for personal research workbench."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urlparse

from a_stock_tracker.auth import (
    Actor,
    AuthError,
    check_production_auth_config,
    create_demo_actor,
    create_owner_actor,
)
from a_stock_tracker.evidence import REPORT_FIELDS, research_prompt
from a_stock_tracker.paths import DATA_DIR, workspace_path
from a_stock_tracker.research import fmt_number
from a_stock_tracker.services import (
    ServiceError,
    delete_watch,
    dismiss_update_job,
    get_company_context,
    get_home,
    get_peer_discover,
    get_update_job,
    job_status_label,
    list_update_jobs,
    mark_seen,
    peer_anchors,
    request_company_update,
    request_peer_update,
    request_watch_update,
    save_watch,
    set_watch_status,
)
from a_stock_tracker.workspace import WorkspaceError

RAW_MODE = os.getenv("APP_MODE", "demo")
if RAW_MODE not in ("demo", "production"):
    raise ValueError(f"Invalid APP_MODE: '{RAW_MODE}'. Must be 'demo' or 'production'.")
APP_MODE: Literal["demo", "production"] = "production" if RAW_MODE == "production" else "demo"
STATE_DIR = workspace_path(APP_MODE)
RESEARCH_DATA_DIR = DATA_DIR
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8550")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8550"))
FACT_LIMITS = (
    "尚未核查：资产质量与减值、负债与杠杆、现金流、一次性损益和审计意见。"
    "低PB或高历史ROE不能回答是否值得买入。"
)

os.environ.setdefault("FLET_SESSION_TIMEOUT", "3600")
os.environ.setdefault("FLET_OAUTH_STATE_TIMEOUT", "600")
FLET_SESSION_TIMEOUT = int(os.environ.get("FLET_SESSION_TIMEOUT", "3600"))
FLET_OAUTH_STATE_TIMEOUT = int(os.environ.get("FLET_OAUTH_STATE_TIMEOUT", "600"))
if FLET_SESSION_TIMEOUT <= 0 or FLET_OAUTH_STATE_TIMEOUT <= 0:
    raise ValueError("FLET_SESSION_TIMEOUT and FLET_OAUTH_STATE_TIMEOUT must be positive integers.")

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_ALLOWED_USER_ID = os.getenv("GITHUB_ALLOWED_USER_ID", "")

# Verify production startup conditions immediately
if APP_MODE == "production":
    check_production_auth_config(GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_ALLOWED_USER_ID)
elif APP_MODE == "demo":
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"Demo mode only allows loopback host, got: {HOST}")


def build_app():
    import flet as ft

    # Design System Tokens (Impeccable Operate Mode)
    COLOR_PRIMARY = "#0F766E"  # Slate teal / authoritative financial accent
    COLOR_PRIMARY_CONTAINER = "#F0FDFA"  # Teal 50
    COLOR_BG = "#F8FAFC"  # Slate 50
    COLOR_SURFACE = "#FFFFFF"
    COLOR_BORDER = "#E2E8F0"  # Slate 200
    COLOR_TEXT_PRIMARY = "#0F172A"  # Slate 900
    COLOR_TEXT_SECONDARY = "#475569"  # Slate 600
    COLOR_TEXT_MUTED = (
        "#64748B"  # Slate 500 (meets WCAG AA >= 4.5:1 contrast on white and Slate 50)
    )

    async def main(page: ft.Page):
        page.title = "投研工作台"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.fonts = {"NotoSansSC": "fonts/NotoSansSC-Regular.otf"}
        page.theme = ft.Theme(
            font_family="NotoSansSC",
            color_scheme_seed=COLOR_PRIMARY,
            card_theme=ft.CardTheme(
                elevation=0,
                color=COLOR_SURFACE,
                shadow_color=ft.Colors.TRANSPARENT,
                margin=ft.Margin.all(0),
            ),
            navigation_bar_theme=ft.NavigationBarTheme(
                bgcolor=COLOR_SURFACE,
                indicator_color="#CCFBF1",
                elevation=1,
            ),
            divider_theme=ft.DividerTheme(
                color=COLOR_BORDER,
                thickness=1,
                space=16,
            ),
            button_theme=ft.ButtonTheme(
                style=ft.ButtonStyle(
                    padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                    shape=ft.RoundedRectangleBorder(radius=8),
                )
            ),
        )
        page.bgcolor = COLOR_BG
        page.padding = 16
        page.scroll = ft.ScrollMode.AUTO

        # State per page session
        page_state: dict[str, Any] = {
            "actor": create_demo_actor() if APP_MODE == "demo" else None,
            "route": (page.route or "/") if APP_MODE == "demo" else "/login",
            "selected_anchor": "600001.SH",
            "generation": 0,
            "drafts": {},
            "current_form_getter": None,
            "current_company_code": None,
            "current_form_baseline": None,
            "job_poll_task": None,
            "pending_updates": {},
            "connected": True,
            "company_return": "/",
            "scroll_positions": {},
            "expanded_sections": {},
        }

        # Explicit logout in settings handles actor revocation
        loading_content = ft.Column(
            [ft.ProgressRing(width=24, height=24), ft.Text("正在读取资料，请稍候…")]
        )
        content_container = ft.Container(content=loading_content, expand=True)
        login_msg = ft.Text("", size=13, color=ft.Colors.RED_700)

        def remember_draft() -> None:
            getter = page_state.get("current_form_getter")
            code = page_state.get("current_company_code")
            baseline = page_state.get("current_form_baseline")
            if not callable(getter) or not code or baseline is None:
                return
            current = getter()
            drafts = page_state["drafts"]
            existing = drafts.get(code, {})
            revision = page_state["current_company_revision"]
            updated_at = page_state.get("current_company_updated_at")
            base_revision = existing.get("_base_revision")
            conflict = (base_revision is not None and base_revision != revision) or (
                "_base_updated_at" in existing and existing["_base_updated_at"] != updated_at
            )
            if current != baseline or conflict:
                drafts[code] = {
                    **current,
                    "_base_revision": base_revision if base_revision is not None else revision,
                    "_base_updated_at": existing.get("_base_updated_at", updated_at),
                }
            else:
                drafts.pop(code, None)

        async def navigate(route: str, *, from_browser: bool = False):
            page.pop_dialog()  # A dialog belongs to its view, not a later route.
            getter = page_state.get("current_form_getter")
            actor = page_state.get("actor")
            if (
                route != page_state["route"]
                and route != "/login"
                and actor
                and actor.is_valid
                and callable(getter)
                and getter() != page_state.get("current_form_baseline")
            ):
                if from_browser:
                    await page.push_route(page_state["route"])
                current_gen = page_state["generation"]

                async def stay(e):
                    page.pop_dialog()

                async def discard(e):
                    page.pop_dialog()
                    if current_gen != page_state["generation"] or not actor.is_valid:
                        return
                    page_state["drafts"].pop(page_state.get("current_company_code"), None)
                    page_state["current_form_getter"] = None
                    await navigate(route)

                async def save_and_leave(e):
                    page.pop_dialog()
                    if current_gen != page_state["generation"] or not actor.is_valid:
                        return
                    await page_state["save_current_form"](e)
                    if (
                        current_gen == page_state["generation"]
                        and actor.is_valid
                        and getter() == page_state.get("current_form_baseline")
                    ):
                        await navigate(route)

                page.show_dialog(
                    ft.AlertDialog(
                        modal=True,
                        title=ft.Text("有未保存的研究记录"),
                        content=ft.Text("保存成功后才能离开；放弃不会更改已保存记录。"),
                        actions=[
                            ft.TextButton("继续编辑", on_click=stay),
                            ft.TextButton("放弃并离开", on_click=discard),
                            ft.TextButton("保存并离开", on_click=save_and_leave),
                        ],
                    )
                )
                return
            poll = page_state.get("job_poll_task")
            if poll is not None and poll is not asyncio.current_task():
                poll.cancel()
                page_state["job_poll_task"] = None
            # Freeze the destination offset before late scroll events from the old page arrive.
            restore_offset = page_state["scroll_positions"].get(route, 0)
            page_state["generation"] += 1
            if route.startswith("/company/") and not page_state["route"].startswith("/company/"):
                page_state["company_return"] = page_state["route"]
            page_state["route"] = route
            page_state["current_form_getter"] = None
            page_state["current_company_code"] = None
            page_state["current_form_baseline"] = None
            gen = page_state["generation"]
            if not from_browser:
                await page.push_route(route)
            if gen != page_state["generation"] or not page_state["connected"]:
                return
            await render_current_view()
            if gen == page_state["generation"] and page_state["connected"]:
                page.update()
                try:
                    await asyncio.wait_for(
                        page.scroll_to(offset=restore_offset, duration=0),
                        timeout=1.0,
                    )
                except Exception as exc:
                    logging.getLogger(__name__).debug("Scroll restore skipped: %s", exc)

        async def on_route_change(e):
            if e.route != page_state["route"]:
                await navigate(e.route, from_browser=True)

        def on_scroll(e):
            page_state["scroll_positions"][page_state["route"]] = e.pixels

        page.on_route_change = on_route_change
        page.views[0].on_scroll = on_scroll

        async def go_back(e):
            await navigate(page_state["company_return"])

        async def go_home(e):
            await navigate("/")

        async def go_settings(e):
            await navigate("/settings")

        async def go_discover(e):
            await navigate("/discover?" + urlencode({"anchor": page_state["selected_anchor"]}))

        async def handle_login_failure(error_msg: str):
            page_state["generation"] += 1
            page_state.setdefault("drafts", {}).clear()
            page_state["current_form_getter"] = None
            page_state["current_company_code"] = None
            page_state["current_form_baseline"] = None
            old_actor = page_state.get("actor")
            if old_actor:
                old_actor.revoke()
            page_state["actor"] = None
            if APP_MODE == "production":
                page.logout()
            login_msg.value = error_msg
            await navigate("/login")

        # Configure OAuth for production mode (Same-tab authorization flow)
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
                if e.error:
                    await handle_login_failure(f"登录失败: {e.error_description or e.error}")
                    return
                try:
                    user_data = (
                        page.auth.user
                        if (page.auth is not None and hasattr(page.auth, "user"))
                        else None
                    )
                    raw_id = getattr(user_data, "id", None) if user_data is not None else None
                    if raw_id is None and isinstance(user_data, dict):
                        raw_id = user_data.get("id")
                    actor = create_owner_actor(raw_id, GITHUB_ALLOWED_USER_ID)
                    page_state["actor"] = actor
                    login_msg.value = ""
                    await navigate("/")
                except AuthError as exc:
                    await handle_login_failure(f"登录被拒绝: {exc}")

            page.on_login = on_login

        async def on_logout(e):
            poll = page_state.get("job_poll_task")
            if poll is not None:
                poll.cancel()
                page_state["job_poll_task"] = None
            page_state["generation"] += 1
            page_state.setdefault("drafts", {}).clear()
            page_state["current_form_getter"] = None
            page_state["current_company_code"] = None
            page_state["current_form_baseline"] = None
            actor: Actor | None = page_state.get("actor")
            if actor:
                actor.revoke()
            page_state["actor"] = None
            if APP_MODE == "production":
                page.logout()
            content_container.content = await render_login()
            page.update()
            await navigate("/login")

        def removal_button(label, message, operation, gen, actor, *, code=None):
            def live():
                return (
                    gen == page_state["generation"] and actor.is_valid and page_state["connected"]
                )

            async def ask(e):
                if not live():
                    return
                feedback = ft.Text("", color=ft.Colors.RED_700)
                busy = False
                closed = False

                async def cancel(e):
                    nonlocal closed
                    if live() and not busy:
                        closed = True
                        page.pop_dialog()

                async def confirm(e):
                    nonlocal busy, closed
                    if not live() or busy or closed:
                        return
                    busy = True
                    closed = True
                    try:
                        await asyncio.to_thread(operation)
                    except Exception:
                        if live():
                            feedback.value = "操作未确认：记录可能已变化或授权失效，请返回后重新查看；不会自动重试。"
                            page.update()
                        return
                    finally:
                        busy = False
                    if not live():
                        return
                    if code:
                        page_state["drafts"].pop(code, None)
                        page_state["current_form_getter"] = None
                    page_state["notice"] = (
                        "已删除个人研究记录，历史扫描保留。"
                        if code
                        else "已从列表清理失败任务，防重放记录保留。"
                    )
                    await navigate("/")

                page.show_dialog(
                    ft.AlertDialog(
                        modal=True,
                        title=ft.Text(label),
                        content=ft.Column([ft.Text(message), feedback], tight=True),
                        actions=[
                            ft.TextButton("取消", on_click=cancel),
                            ft.TextButton("确认操作", on_click=confirm),
                        ],
                    )
                )

            return ft.Button(label, on_click=ask)

        # --- Views ---
        async def render_login():
            async def trigger_login(e):
                if provider:
                    await page.login(
                        provider,
                        on_open_authorization_url=on_open_auth_url,
                        redirect_to_page=True,
                    )

            return ft.Column(
                controls=[
                    ft.Text(
                        "欢迎使用个人投研工作台",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        color=COLOR_TEXT_PRIMARY,
                    ),
                    ft.Text(
                        "生产模式需要所有者登录以访问私人数据", size=14, color=COLOR_TEXT_SECONDARY
                    ),
                    ft.Button(
                        "使用 GitHub 登录",
                        icon=ft.Icons.LOGIN,
                        on_click=trigger_login,
                    ),
                    login_msg,
                ],
                spacing=16,
            )

        pending_message = "上次提交尚未确认；可先查看最近任务，或重试确认（复用原请求编号）。"

        async def submit_update(
            anchor: str | None,
            button: ft.Button | ft.TextButton,
            feedback: ft.Text,
            gen: int,
            actor: Actor,
            *,
            company_code: str | None = None,
        ) -> dict[str, Any] | None:
            def live() -> bool:
                return (
                    gen == page_state["generation"] and actor.is_valid and page_state["connected"]
                )

            if not live() or button.disabled:
                return None
            button.disabled = True
            feedback.value = "正在确认是否受理，请勿重复提交；离开页面不会取消已受理任务。"
            feedback.color = ft.Colors.BLUE_GREY_700
            page.update()
            pending = page_state["pending_updates"]
            pending_key = (
                ("company", company_code.strip().upper()) if company_code is not None else anchor
            )
            request_id = pending.setdefault(pending_key, uuid.uuid4().hex)
            try:
                if company_code is not None:
                    job = await asyncio.to_thread(
                        request_company_update,
                        actor,
                        company_code,
                        request_id,
                        STATE_DIR,
                        APP_MODE,
                        RESEARCH_DATA_DIR,
                    )
                elif anchor is None:
                    job = await asyncio.to_thread(
                        request_watch_update,
                        actor,
                        request_id,
                        STATE_DIR,
                        APP_MODE,
                        RESEARCH_DATA_DIR,
                    )
                else:
                    job = await asyncio.to_thread(
                        request_peer_update,
                        actor,
                        anchor,
                        request_id,
                        STATE_DIR,
                        APP_MODE,
                        RESEARCH_DATA_DIR,
                    )
            except (ServiceError, WorkspaceError, AuthError) as exc:
                message = (
                    f"未取得受理回执：{exc}。可查看最近任务，处理阻断后重试确认（复用原请求编号）。"
                )
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Update receipt unavailable (%s)", type(exc).__name__
                )
                message = "提交结果未确认，可能已受理。可查看最近任务，或重试确认（复用原请求编号），不会自动重放。"
            else:
                if live():
                    if pending.get(pending_key) == request_id:
                        pending.pop(pending_key)
                    feedback.value = "已受理，可留在此页查看进度，也可稍后回来。"
                    return job
                # Keep the ID for a returning page to resolve, never silently start anew.
                return None
            if live():
                feedback.value = message
                feedback.color = ft.Colors.RED_700
                button.disabled = False
                page.update()
            return None

        def update_progress(jobs: list[dict[str, Any]], gen: int, actor: Actor) -> ft.Control:
            """Read-only progress in the originating view; never resubmit on reconnect."""
            if not jobs:
                return ft.Container()
            job = jobs[0]
            active = job["status"] in ("queued", "running")
            scope_label = (
                f"公司 {job['company_code']}"
                if job.get("company_code")
                else f"关注更新（{len(job.get('codes', []))}家）"
                if job["kind"] == "watch"
                else f"同业扫描 {job['anchor']}"
            )
            status = ft.Text(
                f"最近更新：{job_status_label(job)} · {scope_label} · 数据日 {job['target_date']}",
                size=13,
            )

            async def open_details(e):
                if gen == page_state["generation"] and actor.is_valid and page_state["connected"]:
                    await navigate(f"/jobs/{job['job_id']}")

            async def show_result(e):
                if (
                    gen != page_state["generation"]
                    or not actor.is_valid
                    or not page_state["connected"]
                ):
                    return
                if job.get("company_code"):
                    await navigate(f"/company/{job['company_code']}")
                elif job["kind"] == "peer":
                    await navigate(
                        "/discover?" + urlencode({"anchor": job["anchor"], "job": job["job_id"]})
                    )
                else:
                    await navigate(page_state["route"])

            result = ft.Button(
                "查看更新结果",
                on_click=show_result,
                visible=not active and job["status"] == "succeeded",
            )

            async def poll():
                try:
                    while active:
                        await asyncio.sleep(2)
                        if (
                            gen != page_state["generation"]
                            or not actor.is_valid
                            or not page_state["connected"]
                        ):
                            return
                        latest = await asyncio.to_thread(
                            get_update_job, actor, job["job_id"], STATE_DIR, APP_MODE
                        )
                        if (
                            gen != page_state["generation"]
                            or not actor.is_valid
                            or not page_state["connected"]
                        ):
                            return
                        status.value = f"最近更新：{job_status_label(latest)} · {scope_label} · 数据日 {latest['target_date']}"
                        if latest.get("error_summary"):
                            status.value += f" · {latest['error_summary']}"
                        if latest["status"] not in ("queued", "running"):
                            result.visible = latest["status"] == "succeeded"
                            page.update()
                            return
                        page.update()
                except asyncio.CancelledError:
                    return
                except (ServiceError, WorkspaceError, AuthError):
                    if (
                        gen == page_state["generation"]
                        and actor.is_valid
                        and page_state["connected"]
                    ):
                        status.value = "进度暂不可读取；可查看更新详情，不会重复提交。"
                        page.update()

            if active:
                page_state["job_poll_task"] = asyncio.create_task(poll())
            return ft.Column(
                [
                    status,
                    ft.Text(job.get("error_summary") or "", color=ft.Colors.RED_700),
                    ft.Row([result, ft.TextButton("更新详情", on_click=open_details)], wrap=True),
                ],
                spacing=4,
            )

        def company_entry(gen: int, actor: Actor) -> ft.Control:
            code = ft.TextField(
                label="指定公司代码",
                hint_text="演示代码 600002" if APP_MODE == "demo" else "例如 600900 或 000786",
                value="",
                max_length=9,
            )
            feedback = ft.Text("", size=13)
            button = ft.Button("获取公司资料", height=48)

            async def submit(e):
                job = await submit_update(
                    None, button, feedback, gen, actor, company_code=code.value or ""
                )
                if job:
                    await navigate("/")

            button.on_click = submit
            code.on_submit = submit
            return ft.Column(
                [
                    code,
                    button,
                    feedback,
                    ft.Text("沪深主板非金融；点击后核验资料，再决定是否加入研究。", size=12),
                ],
                spacing=6,
            )

        async def render_home(gen: int):
            actor: Actor | None = page_state.get("actor")
            if not actor or not actor.is_valid:
                return await render_login()

            data = await asyncio.to_thread(get_home, actor, STATE_DIR, APP_MODE)
            if gen != page_state["generation"] or not actor.is_valid:
                return await render_login()
            jobs = await asyncio.to_thread(list_update_jobs, actor, STATE_DIR, APP_MODE)
            if gen != page_state["generation"] or not actor.is_valid:
                return await render_login()
            items_controls: list[ft.Control] = []
            paused_controls: list[ft.Control] = []
            routine_controls: list[ft.Control] = []
            status_feedback = ft.Text(
                pending_message if None in page_state["pending_updates"] else "",
                color=ft.Colors.RED_700,
            )
            active_count = sum(item["status"] != "paused" for item in data["watch_items"])
            update_button = ft.Button("更新资料", height=48, disabled=not 0 < active_count <= 50)

            async def submit_watch(e):
                job = await submit_update(None, update_button, status_feedback, gen, actor)
                if job:
                    await navigate("/")

            update_button.on_click = submit_watch
            last_group = None
            tier_order = {
                "anomaly": 0,
                "risk_change": 1,
                "fact_change": 2,
                "date_change": 3,
                "no_change": 4,
            }
            tier_titles = {
                "anomaly": "数据异常与缺口",
                "risk_change": "公司状态变化（请核查）",
                "fact_change": "指标与资料变化",
                "date_change": "仅数据日更新",
                "no_change": "暂无未阅变化",
            }

            for it in sorted(
                data["watch_items"],
                key=lambda item: (
                    tier_order.get(item.get("change_tier", "no_change"), 4),
                    item["status"] != "research",
                    item["code"],
                ),
            ):
                code = it["code"]
                status_color = (
                    "#1D4ED8"
                    if it["status"] == "research"
                    else ("#475569" if it["status"] == "observe" else "#64748B")
                )
                status_bg = (
                    "#EFF6FF"
                    if it["status"] == "research"
                    else ("#F1F5F9" if it["status"] == "observe" else "#F8FAFC")
                )
                status_border = (
                    "#BFDBFE"
                    if it["status"] == "research"
                    else ("#CBD5E1" if it["status"] == "observe" else "#E2E8F0")
                )
                status_chip = ft.Container(
                    content=ft.Text(
                        "继续研究"
                        if it["status"] == "research"
                        else ("等待证据" if it["status"] == "observe" else "暂不研究"),
                        size=12,
                        weight=ft.FontWeight.W_500,
                        color=status_color,
                    ),
                    bgcolor=status_bg,
                    border=ft.Border.all(1, status_border),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                    border_radius=12,
                )

                async def on_card_click(e, c=code):
                    if (
                        gen == page_state["generation"]
                        and actor.is_valid
                        and page_state["connected"]
                    ):
                        await navigate(f"/company/{c}")

                async def change_status(e, item=it):
                    if (
                        gen != page_state["generation"]
                        or not actor.is_valid
                        or not page_state["connected"]
                        or e.control.disabled
                    ):
                        return
                    e.control.disabled = True
                    page.update()
                    target = "observe" if item["status"] == "paused" else "paused"
                    try:
                        await asyncio.to_thread(
                            set_watch_status,
                            actor,
                            item["code"],
                            target,
                            item["revision"],
                            item["updated_at"],
                            STATE_DIR,
                            APP_MODE,
                        )
                        if (
                            gen == page_state["generation"]
                            and actor.is_valid
                            and page_state["connected"]
                        ):
                            page_state["notice"] = (
                                "已恢复为等待证据，笔记和已阅基准保留。"
                                if target == "observe"
                                else "已暂停关注，不再计入需要复看；笔记和已阅基准保留。"
                            )
                            await navigate("/")
                    except Exception:
                        if (
                            gen == page_state["generation"]
                            and actor.is_valid
                            and page_state["connected"]
                        ):
                            status_feedback.value = (
                                "状态修改未确认，记录可能已变化；请重新打开首页核对，不会自动重试。"
                            )
                            e.control.disabled = False
                            page.update()

                async def mark_card_seen(e, item=it):
                    if (
                        gen != page_state["generation"]
                        or not actor.is_valid
                        or not page_state["connected"]
                        or e.control.disabled
                    ):
                        return
                    e.control.disabled = True
                    page.update()
                    try:
                        await asyncio.to_thread(
                            mark_seen,
                            actor=actor,
                            code=item["code"],
                            displayed_run_id=item["usable_run_id"],
                            expected_revision=item["revision"],
                            expected_updated_at=item["updated_at"],
                            state_dir=STATE_DIR,
                            mode=APP_MODE,
                        )
                        if (
                            gen == page_state["generation"]
                            and actor.is_valid
                            and page_state["connected"]
                        ):
                            page_state["notice"] = f"{item['name']}：已标记本次已阅。"
                            await navigate("/")
                    except Exception:
                        if (
                            gen == page_state["generation"]
                            and actor.is_valid
                            and page_state["connected"]
                        ):
                            status_feedback.value = (
                                "标记已阅未确认，资料或记录可能已变化；请重新打开首页核对。"
                            )
                            e.control.disabled = False
                            page.update()

                tier = it.get("change_tier", "no_change")
                if it["status"] == "paused":
                    destination = paused_controls
                else:
                    destination = items_controls if it["needs_attention"] else routine_controls
                    group = tier_titles.get(tier, "暂无未阅变化")
                    if group != last_group:
                        if tier in ("anomaly", "risk_change"):
                            header: ft.Control = ft.Row(
                                controls=[
                                    ft.Icon(
                                        ft.Icons.WARNING_AMBER_ROUNDED,
                                        color=ft.Colors.RED_900,
                                        size=18,
                                    ),
                                    ft.Text(
                                        group,
                                        size=16,
                                        weight=ft.FontWeight.BOLD,
                                        color=ft.Colors.RED_900,
                                    ),
                                ],
                                spacing=4,
                            )
                        elif tier == "fact_change":
                            header = ft.Text(
                                group,
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.ORANGE_900,
                            )
                        elif tier == "date_change":
                            header = ft.Text(
                                group,
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.BLUE_GREY_800,
                            )
                        else:
                            header = ft.Text(
                                group,
                                size=16,
                                weight=ft.FontWeight.BOLD,
                            )
                        destination.append(header)
                        last_group = group
                change_desc = it["change_summary"] if it["has_change"] else "覆盖字段暂无未阅变化"
                desc_color = (
                    ft.Colors.RED_900
                    if tier in ("anomaly", "risk_change")
                    else (
                        ft.Colors.ORANGE_900
                        if tier == "fact_change"
                        else (
                            ft.Colors.BLUE_GREY_700 if tier == "date_change" else ft.Colors.GREY_700
                        )
                    )
                )
                desc_weight = (
                    ft.FontWeight.BOLD
                    if tier in ("anomaly", "risk_change", "fact_change")
                    else ft.FontWeight.NORMAL
                )
                destination.append(
                    ft.Card(
                        semantic_container=False,
                        content=ft.Container(
                            padding=14,
                            bgcolor=COLOR_SURFACE,
                            border=ft.Border.all(1, COLOR_BORDER),
                            border_radius=8,
                            on_click=on_card_click,
                            content=ft.Column(
                                controls=[
                                    ft.Row(
                                        controls=[
                                            ft.Text(
                                                f"{it['name']} ({code})",
                                                expand=True,
                                                size=16,
                                                weight=ft.FontWeight.BOLD,
                                                color=COLOR_TEXT_PRIMARY,
                                            ),
                                            status_chip,
                                        ],
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    ),
                                    ft.Text(
                                        change_desc,
                                        size=13,
                                        color=desc_color,
                                        weight=desc_weight,
                                    ),
                                    ft.Text(
                                        (
                                            "上次可用资料 · "
                                            if tier == "anomaly" and it.get("usable_run_id")
                                            else ""
                                        )
                                        + f"PB：{fmt_number(it.get('pb'))}倍 | 3年ROE：{fmt_number(it.get('roe_mean'))}%",
                                        size=13,
                                        color=COLOR_TEXT_SECONDARY,
                                        semantics_label=(
                                            (
                                                "上次可用资料 · "
                                                if tier == "anomaly" and it.get("usable_run_id")
                                                else ""
                                            )
                                            + f"PB：{fmt_number(it.get('pb'))}倍 | 3年ROE：{fmt_number(it.get('roe_mean'))}%"
                                        ),
                                    ),
                                    ft.Text(
                                        (
                                            f"{'该次财报' if tier == 'anomaly' else '最新财报'}：{it['latest_report_period']} | "
                                            f"扣非同比：{fmt_number(it.get('dt_netprofit_yoy'))}% | "
                                            f"经营现金流：{fmt_number(it.get('ocfps'))}元/股（累计）"
                                        )
                                        if it.get("latest_report_period")
                                        else "最新财报：尚未取得，可主动更新",
                                        size=13,
                                        color=COLOR_TEXT_SECONDARY,
                                        semantics_label=(
                                            (
                                                f"{'该次财报' if tier == 'anomaly' else '最新财报'}：{it['latest_report_period']} | "
                                                f"扣非同比：{fmt_number(it.get('dt_netprofit_yoy'))}% | "
                                                f"经营现金流：{fmt_number(it.get('ocfps'))}元/股（累计）"
                                            )
                                            if it.get("latest_report_period")
                                            else "最新财报：尚未取得，可主动更新"
                                        ),
                                    ),
                                    ft.Text(
                                        it.get("reason")
                                        or it.get("research_prompt", {}).get(
                                            "action", "先核查业务与盈利质量"
                                        ),
                                        size=14,
                                        color="#334155",
                                        max_lines=2,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    *(
                                        [
                                            ft.Text(
                                                f"上次下一步：{it['next_check']}",
                                                size=13,
                                                color=COLOR_TEXT_SECONDARY,
                                                max_lines=2,
                                                overflow=ft.TextOverflow.ELLIPSIS,
                                            )
                                        ]
                                        if it.get("next_check")
                                        else []
                                    ),
                                    ft.Text(
                                        f"数据日：{it.get('valuation_date', '暂无')}",
                                        size=12,
                                        color=COLOR_TEXT_MUTED,
                                    ),
                                    ft.Row(
                                        controls=[
                                            ft.Button("查看详情", on_click=on_card_click),
                                            *(
                                                [ft.Button("标记本次已阅", on_click=mark_card_seen)]
                                                if tier == "date_change" and it.get("usable_run_id")
                                                else []
                                            ),
                                            ft.Button(
                                                "恢复为等待证据"
                                                if it["status"] == "paused"
                                                else "暂停关注",
                                                on_click=change_status,
                                            ),
                                        ],
                                        wrap=True,
                                        spacing=8,
                                    ),
                                    *(
                                        [
                                            ft.Text(
                                                "已阅确认界面所示事实并更新对照基准，不代表已核验公告原文或作出投资判断。",
                                                size=12,
                                                color=COLOR_TEXT_MUTED,
                                            )
                                        ]
                                        if tier == "date_change" and it.get("usable_run_id")
                                        else []
                                    ),
                                ],
                                spacing=6,
                            ),
                        ),
                    )
                )

            if paused_controls and not items_controls and not routine_controls:
                items_controls.append(ft.Text("当前关注均已暂停；展开下方列表可恢复为等待证据。"))

            if not data["watch_items"]:

                async def go_to_discover(e):
                    if (
                        gen != page_state["generation"]
                        or not actor.is_valid
                        or not page_state["connected"]
                    ):
                        return
                    await go_discover(e)

                items_controls.append(
                    ft.Card(
                        semantic_container=False,
                        content=ft.Container(
                            padding=24,
                            bgcolor=COLOR_SURFACE,
                            border=ft.Border.all(1, COLOR_BORDER),
                            border_radius=10,
                            content=ft.Column(
                                controls=[
                                    ft.Text(
                                        "暂无关注的公司",
                                        size=16,
                                        weight=ft.FontWeight.BOLD,
                                        color=COLOR_TEXT_PRIMARY,
                                    ),
                                    ft.Text(
                                        "输入公司代码，或在『发现候选』选择参照；取得资料后可一键关注。",
                                        size=13,
                                        color=COLOR_TEXT_SECONDARY,
                                        text_align=ft.TextAlign.CENTER,
                                    ),
                                    ft.Button("前往发现候选", on_click=go_to_discover),
                                ],
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=8,
                            ),
                        ),
                    )
                )

            paused_key = ("/", "paused_watch")
            paused_body = ft.Column(
                paused_controls, visible=page_state["expanded_sections"].get(paused_key, False)
            )
            paused_button = ft.Button(
                f"{'收起' if paused_body.visible else '显示'}已暂停 ({len(paused_controls)})"
            )

            async def toggle_paused(e):
                if (
                    gen != page_state["generation"]
                    or not actor.is_valid
                    or not page_state["connected"]
                ):
                    return
                paused_body.visible = not paused_body.visible
                page_state["expanded_sections"][paused_key] = paused_body.visible
                paused_button.content = (
                    f"{'收起' if paused_body.visible else '显示'}已暂停 ({len(paused_controls)})"
                )
                page.update()

            paused_button.on_click = toggle_paused

            routine_key = ("/", "routine_watch")
            routine_body = ft.Column(
                routine_controls, visible=page_state["expanded_sections"].get(routine_key, False)
            )

            async def toggle_routine(e):
                if gen == page_state["generation"] and actor.is_valid and page_state["connected"]:
                    routine_body.visible = not routine_body.visible
                    page_state["expanded_sections"][routine_key] = routine_body.visible
                    page.update()

            routine_button = ft.Button("查看其余研究（含估值与日期变化）", on_click=toggle_routine)
            job_controls: list[ft.Control] = []
            for job in jobs:

                async def open_job(e, job_id=job["job_id"]):
                    await navigate(f"/jobs/{job_id}")

                job_controls.append(
                    ft.Button(
                        f"{job['anchor'] if job['kind'] == 'peer' else '固定关注'} · {job['target_date']} · {job_status_label(job)}",
                        on_click=open_job,
                    )
                )

            return ft.Column(
                controls=[
                    ft.Container(
                        padding=16,
                        bgcolor=COLOR_PRIMARY_CONTAINER,
                        border=ft.Border.all(1, "#CCFBF1"),
                        border_radius=10,
                        content=ft.Row(
                            controls=[
                                ft.Column(
                                    [
                                        ft.Text(
                                            "我的研究",
                                            size=18,
                                            weight=ft.FontWeight.BOLD,
                                            color="#134E4A",
                                        ),
                                        ft.Text(
                                            f"估值基准日：{data['valuation_date']} | 待处理：{data['important_review_count']} 家",
                                            size=13,
                                            color=COLOR_PRIMARY,
                                        ),
                                    ],
                                    expand=True,
                                    spacing=4,
                                ),
                                update_button,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                    ),
                    ft.Text(page_state.pop("notice", ""), color=ft.Colors.GREEN_700),
                    ft.Text(
                        f"更新 {active_count} 家未暂停公司；最多50家。"
                        if active_count
                        else "从熟悉的公司开始，或发现同业候选。",
                        size=13,
                    ),
                    company_entry(gen, actor),
                    update_progress(jobs, gen, actor),
                    *(
                        [ft.Button("前往同业扫描", on_click=go_discover)]
                        if data["total_watch_count"]
                        else []
                    ),
                    ft.Divider(height=16),
                    ft.Text(
                        f"关注清单 ({data['total_watch_count']})",
                        size=15,
                        weight=ft.FontWeight.W_600,
                    ),
                    status_feedback,
                    *items_controls,
                    *(
                        [ft.Text("暂无重要待处理事项；估值和日期变化仍可查看。")]
                        if data["watch_items"] and not items_controls
                        else []
                    ),
                    *([routine_button, routine_body] if routine_controls else []),
                    *([paused_button, paused_body] if paused_controls else []),
                    ft.Divider(),
                    *(
                        [ft.ExpansionTile(title="更新记录", controls=job_controls)]
                        if job_controls
                        else []
                    ),
                ],
                spacing=8,
            )

        async def render_discover(gen: int):
            actor: Actor | None = page_state.get("actor")
            if not actor or not actor.is_valid:
                return await render_login()

            try:
                allowed = await asyncio.to_thread(peer_anchors, actor, APP_MODE)
                anchor_error = ""
            except ServiceError as exc:
                allowed, anchor_error = [], str(exc)
            if gen != page_state["generation"] or not actor.is_valid:
                return await render_login()
            codes = {entry["code"] for entry in allowed}
            available_codes = {
                entry["code"] for entry in allowed if not entry.get("discovery_unavailable")
            }
            params = parse_qs(urlparse(page_state["route"]).query)
            anchor = params.get("anchor", [page_state["selected_anchor"]])[0]
            job_id = params.get("job", [None])[0]
            run_id = page_state.get("discover_boards", {}).get(page_state["route"])
            if anchor not in codes:
                anchor = next(
                    (entry["code"] for entry in allowed if entry["code"] in available_codes), ""
                )
                page_state["selected_anchor"] = anchor
            page_state["selected_anchor"] = anchor
            try:
                disc_data: dict[str, Any] = (
                    await asyncio.to_thread(
                        get_peer_discover,
                        actor,
                        anchor,
                        STATE_DIR,
                        APP_MODE,
                        job_id=job_id,
                        run_id=run_id,
                    )
                    if anchor
                    else {"has_run": False, "error": anchor_error or "暂无参照标的"}
                )
            except ServiceError as exc:
                disc_data = {"has_run": False, "error": str(exc)}
            if gen == page_state["generation"] and actor.is_valid and disc_data.get("has_run"):
                page_state.setdefault("discover_boards", {})[page_state["route"]] = disc_data[
                    "run_id"
                ]
            if gen != page_state["generation"] or not actor.is_valid:
                return await render_login()

            async def on_anchor_select(e):
                if (
                    gen == page_state["generation"]
                    and actor.is_valid
                    and page_state["connected"]
                    and anchor_field.value in codes
                ):
                    page_state["selected_anchor"] = anchor_field.value
                    await navigate("/discover?" + urlencode({"anchor": anchor_field.value}))

            anchor_field = ft.Dropdown(
                label="参照公司",
                value=anchor if anchor in codes else None,
                options=[
                    ft.dropdown.Option(
                        entry["code"],
                        f"{entry['name']} ({entry['code']})"
                        + (" · 暂不适用" if entry.get("discovery_unavailable") else ""),
                        disabled=bool(entry.get("discovery_unavailable")),
                    )
                    for entry in allowed
                ],
                on_select=on_anchor_select,
                disabled=not allowed,
            )
            update_feedback = ft.Text(
                pending_message if anchor in page_state["pending_updates"] else "",
                color=ft.Colors.RED_700,
            )
            submit_button = ft.Button("查找同业", height=48, disabled=anchor not in available_codes)

            async def on_submit(e):
                job = await submit_update(anchor, submit_button, update_feedback, gen, actor)
                if job:
                    await navigate(
                        "/discover?" + urlencode({"anchor": anchor, "job": job["job_id"]})
                    )

            submit_button.on_click = on_submit

            async def refresh_result(e):
                route = "/discover?" + urlencode({"anchor": anchor})
                page_state.setdefault("discover_boards", {}).pop(route, None)
                await navigate(route)

            def section(title: str, controls: list[ft.Control]) -> ft.Column:
                key = (page_state["route"], title)
                body = ft.Column(controls, visible=page_state["expanded_sections"].get(key, False))

                async def toggle(e):
                    if gen == page_state["generation"] and actor.is_valid:
                        body.visible = not body.visible
                        page_state["expanded_sections"][key] = body.visible
                        page.update()

                return ft.Column([ft.Button(title, on_click=toggle), body])

            def exclusion_text(row: dict[str, Any]) -> str:
                labels = {
                    "KNOWN_ST_WARNING": "已知风险警示",
                    "INVALID_PB": "PB缺失或非正值",
                    "NON_POSITIVE_ROE_MEAN": "三年ROE均值非正值",
                    "FINANCIAL_REQUEST_FAILED": "财务核查失败",
                    "MISSING_THREE_ANNUAL_REPORTS": "缺少三份年报",
                    "NON_CONSECUTIVE_ANNUAL_REPORTS": "年报年份不连续",
                    "LATEST_ANNUAL_REPORT_TOO_OLD": "最新年报过旧",
                    "AMBIGUOUS_VERSION": "年报版本有歧义",
                    "INVALID_ROE": "ROE数据无效",
                    "qualified": "符合本次筛选字段要求",
                }
                reasons = row.get("exclusions") or row.get("eligibility_reasons") or []
                return (
                    "；".join(
                        labels.get(str(r).split(":")[0], "其他数据缺口，需核查来源")
                        for r in reasons
                    )
                    or "未记录"
                )

            def fact_card(row: dict[str, Any], result: dict[str, Any], ranked: bool = True):
                code = row.get("code") or row.get("ts_code") or "未知代码"
                rank: dict[str, Any] = (
                    next((r for r in result["results"]["ranking"] if r.get("code") == code), {})
                    if ranked
                    else {}
                )
                status = disc_data.get("watch_statuses", {}).get(code)
                status_text = {
                    "observe": "等待证据",
                    "research": "继续研究",
                    "paused": "暂不研究",
                }.get(status, "未加入")
                feedback = ft.Text("", size=13)
                add_button = ft.Button("关注", height=48, disabled=not row.get("code"))

                async def open_comp(e):
                    if gen == page_state["generation"] and actor.is_valid:
                        await navigate(f"/company/{code}")

                async def add_observation(e):
                    if (
                        gen != page_state["generation"]
                        or not actor.is_valid
                        or not page_state["connected"]
                    ):
                        return
                    add_button.disabled = True
                    page.update()
                    try:
                        await asyncio.to_thread(
                            save_watch,
                            actor,
                            code,
                            result["run_id"],
                            {"status": "observe"},
                            0,
                            STATE_DIR,
                            APP_MODE,
                        )
                        if (
                            gen == page_state["generation"]
                            and actor.is_valid
                            and page_state["connected"]
                        ):
                            add_button.content = "查看关注"
                            add_button.disabled = False
                            add_button.on_click = open_comp
                            feedback.value = "已关注，无需填写笔记；未自动已阅，已有记录不覆盖。"
                            page.update()
                    except (ServiceError, WorkspaceError, AuthError):
                        if gen == page_state["generation"] and actor.is_valid:
                            feedback.value = "加入未确认，请重试；重复加入不会覆盖原记录。"
                            add_button.disabled = False
                            page.update()

                add_button.on_click = add_observation
                annual = row.get("annual_roes") or []
                insights = row.get("fact_insights") or {}
                prompt = research_prompt(row)
                reasons = row.get("exclusions") or row.get("eligibility_reasons") or []
                return ft.Container(
                    padding=14,
                    bgcolor="#FFFDF5" if code == anchor else COLOR_SURFACE,
                    border=ft.Border.all(1, "#F59E0B" if code == anchor else COLOR_BORDER),
                    border_radius=8,
                    content=ft.Column(
                        [
                            ft.Text(
                                f"{row.get('name') or code} ({code})"
                                + (" · 参照公司" if code == anchor else ""),
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=COLOR_TEXT_PRIMARY,
                            ),
                            ft.Text(
                                (
                                    f"研究次序 {rank['position']}"
                                    if rank.get("position")
                                    else "未进入正式排名"
                                )
                                + f" · {status_text}",
                                size=13,
                                color=COLOR_TEXT_SECONDARY,
                            ),
                            ft.Text(
                                f"PB {fmt_number(row.get('pb'))} 倍 · ROE三年均值 {fmt_number(row.get('roe_mean'))}%",
                                weight=ft.FontWeight.W_600,
                                color=COLOR_TEXT_PRIMARY,
                            ),
                            ft.Text(
                                f"入选线索：PB 第 {fmt_number(rank.get('pb_rank'), 1)} 名，历史 ROE 第 {fmt_number(rank.get('roe_rank'), 1)} 名。"
                                if rank
                                else "资料尚不足以形成同业排名。",
                                size=13,
                                color=COLOR_TEXT_SECONDARY,
                            ),
                            ft.Text(f"待核查：{prompt['action']}", size=13, color="#334155"),
                            ft.Text(
                                insights.get("roe_trend", "ROE趋势：资料有缺口，暂不判断"),
                                size=13,
                                color=COLOR_TEXT_SECONDARY,
                            ),
                            ft.Text(
                                "系统未核查业务可比性，行业标签不能证明业务相似。",
                                size=13,
                                color=COLOR_TEXT_SECONDARY,
                            ),
                            ft.Row(
                                [
                                    ft.Button("查看关注" if status else "查看", on_click=open_comp),
                                    *([] if status else [add_button]),
                                ],
                                wrap=True,
                                spacing=8,
                            ),
                            feedback,
                            ft.ExpansionTile(
                                title="支持事实与下一步",
                                controls=[
                                    ft.Text(
                                        f"估值日：{row.get('valuation_date') or result.get('valuation_date') or '未记录'}",
                                        size=12,
                                    ),
                                    *[
                                        ft.Text(
                                            f"{str(a.get('period') or '')[:4] or '年度未知'}年 ROE {fmt_number(a.get('roe_waa'))}%",
                                            size=14,
                                        )
                                        for a in annual
                                    ],
                                    *([ft.Text("逐年ROE缺失，不补零")] if not annual else []),
                                    ft.Text(
                                        insights.get("pb_comparison", "PB中位数暂不比较：缺少事实")
                                    ),
                                    ft.Text(prompt["why"], size=13),
                                    ft.Text("下一步：" + prompt["next"], size=13),
                                    *(
                                        [
                                            ft.Text(
                                                "排除/缺口：" + exclusion_text(row),
                                                color=ft.Colors.AMBER_900,
                                            )
                                        ]
                                        if not rank and reasons and reasons != ["qualified"]
                                        else []
                                    ),
                                ],
                            ),
                        ],
                        spacing=6,
                    ),
                )

            def evidence(result: dict[str, Any]) -> list[ft.Control]:
                scope = result.get("scope") or {}
                rows = result.get("rows", [])
                present_codes = {r.get("code") for r in rows}
                missing = [c for c in scope.get("selected_codes", []) if c not in present_codes]
                controls: list[ft.Control] = [
                    *(
                        [ft.Text("本次零合格候选；请核对业务排除与数据缺口，不改写为成功。")]
                        if not result["results"]["ranking"]
                        else []
                    ),
                    *(
                        [ft.Text("本次无原关注池外合格对象，不生成新的正式前三。")]
                        if result["results"].get("outside_watchlist_qualified_count") == 0
                        else []
                    ),
                    ft.Text(
                        f"行业：{scope.get('industry') or '未记录'} · 来源：{result.get('source')}"
                    ),
                    ft.Text(
                        f"枚举 {scope.get('enumerated_count', '未记录')} 家 / 截取 {len(scope.get('selected_codes', []))} 家 / 合格 {len(result['results']['ranking'])} 家"
                    ),
                    ft.Text(
                        "沪深主板、TuShare粗行业、按市值最多50家；偏向大市值，不是全行业或全市场。行业标签不能证明业务可比。"
                    ),
                    ft.Text(
                        "缺少估值："
                        + str(scope.get("excluded_missing_valuation") or "无记录")
                        + "；超出上限："
                        + str(scope.get("excluded_by_cap") or "无记录")
                    ),
                    ft.Text("已选但缺少公司行：" + ("、".join(missing) or "无")),
                    ft.Text(
                        "PB升序名次与三年ROE均值降序名次取平均，并列取平均名次；小分差不代表价值显著不同。"
                    ),
                    ft.Text(
                        "低PB需核查资产质量；高ROE需核查杠杆、净资产及一次性收益；逐年值防止均值遮盖下行。均为待核查问题，不是已发现风险或买入建议。"
                    ),
                    ft.Text("\n".join(result.get("review_lines", []))),
                ]
                for row in rows:
                    rank: dict[str, Any] = next(
                        (
                            r
                            for r in result["results"]["ranking"]
                            if r.get("code") == row.get("code")
                        ),
                        {},
                    )
                    annual = row.get("annual_roes") or []
                    controls.extend(
                        [
                            ft.Text(
                                f"{row.get('name') or row.get('code')} · {row.get('code')}",
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                f"PB名次 {fmt_number(rank.get('pb_rank'), 1)} / ROE名次 {fmt_number(rank.get('roe_rank'), 1)} / 平均名次 {fmt_number(rank.get('research_order'), 1)}"
                            ),
                            ft.Text("排除/核查：" + exclusion_text(row)),
                            ft.Text(
                                f"估值来源 {row.get('valuation_source') or '未记录'} / 财务来源 {row.get('financial_source') or '未记录'} / 核查 {row.get('financial_checked_at') or '未记录'}"
                            ),
                            *[
                                ft.Text(
                                    f"{a.get('period')} · 公告 {a.get('ann_date') or '未记录'} · 版本 {a.get('update_flag', '未记录')} · 选择依据 {a.get('selection_basis') or '未记录'} · 取得 {a.get('acquired_at') or '未记录'}",
                                    size=12,
                                )
                                for a in annual
                            ],
                        ]
                    )
                return controls

            rows_controls: list[ft.Control] = []
            attempt = disc_data.get("attempt")
            if attempt and (
                attempt.get("error_summary")
                or (attempt.get("result") or {}).get("health") not in (None, "complete")
            ):
                rows_controls.append(
                    ft.Text(
                        f"本次尝试：{attempt['label']} · 目标数据日 {attempt.get('target_date') or '未知'}",
                        weight=ft.FontWeight.BOLD,
                    )
                )
                if attempt.get("error_summary"):
                    rows_controls.append(ft.Text(attempt["error_summary"], color=ft.Colors.RED_700))
                partial = attempt.get("result")
                if partial and partial["health"] != "complete":
                    rows_controls.append(
                        ft.Text("本次部分完成，不作为正式前三；可得事实与缺口见诊断。")
                    )
                    rows_controls.append(
                        section(
                            "查看本次诊断",
                            [
                                *[fact_card(row, partial, ranked=False) for row in partial["rows"]],
                                *evidence(partial),
                            ],
                        )
                    )
                if attempt.get("job_id"):

                    async def open_attempt(e):
                        await navigate(f"/jobs/{attempt['job_id']}")

                    rows_controls.append(ft.Button("查看更新任务", on_click=open_attempt))
            if disc_data.get("has_run"):
                rows_controls.append(
                    ft.Text(
                        (
                            "当前展示上次完整榜"
                            if disc_data.get("showing_previous")
                            else "当前完整榜"
                        )
                        + f" · 估值日 {disc_data['valuation_date']} · 扫描 {disc_data['captured_at'][:10]}",
                        size=13,
                    )
                )
                rows_controls.append(
                    ft.Text(
                        f"比较范围：{disc_data.get('scope', {}).get('industry') or '行业未记录'}；"
                        "沪深主板非金融，按市值最多50家，偏向大市值，非全行业；业务可比性未核查。",
                        size=12,
                    )
                )
                rows = {r.get("code") or r.get("ts_code"): r for r in disc_data["rows"]}
                ranking = disc_data["results"]["ranking"]
                date_sets = {r.get("valuation_date") for r in rows.values()}
                year_sets = {
                    tuple(
                        sorted(str(a.get("period") or "")[:4] for a in (r.get("annual_roes") or []))
                    )
                    for r in rows.values()
                }
                if len(date_sets) > 1 or len(year_sets) > 1:
                    rows_controls.append(
                        ft.Text(
                            "公司估值日或年报覆盖不同，请分别核对；差值不代表同口径优劣。",
                            color=ft.Colors.AMBER_900,
                        )
                    )
                top = disc_data["results"].get("top")
                top = top if isinstance(top, list) else [r.get("code") for r in ranking[:3]]
                featured = list(dict.fromkeys([*top, anchor]))
                if not ranking:
                    rows_controls.append(
                        ft.Text("本次没有合格候选，请展开范围与排除原因；不代表全市场无机会。")
                    )
                if disc_data["results"].get("outside_watchlist_qualified_count") == 0:
                    rows_controls.append(ft.Text("本次无原关注池外合格对象，不扩充研究前三。"))
                rows_controls.extend(
                    fact_card(rows.get(c) or {"code": c, "exclusions": ["缺少事实明细"]}, disc_data)
                    for c in featured
                )
                rows_controls.append(section("展开依据、来源与排除原因", evidence(disc_data)))
                rows_controls.append(
                    section(
                        "展开完整比较",
                        [
                            fact_card(rows.get(r.get("code")) or {"code": r.get("code")}, disc_data)
                            for r in ranking
                            if r.get("code") not in featured
                        ],
                    )
                )
            else:
                rows_controls.append(ft.Text(disc_data.get("message") or "当前无可用完整榜。"))

            recent_jobs = await asyncio.to_thread(list_update_jobs, actor, STATE_DIR, APP_MODE)
            if gen != page_state["generation"] or not actor.is_valid:
                return await render_login()
            relevant_jobs = [
                j
                for j in recent_jobs
                if j["kind"] == "peer"
                and j["anchor"] == anchor
                and (not job_id or j["job_id"] == job_id)
            ]
            return ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text("发现候选", size=18, weight=ft.FontWeight.BOLD),
                            submit_button,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    anchor_field,
                    ft.Text(
                        "选择熟悉的公司，发现值得进一步核查的同业。研究次序不等于买入建议。",
                        size=13,
                    ),
                    *(
                        [
                            ft.Text(
                                "金融行业需独立研究口径，当前参照不适用。",
                                color=ft.Colors.AMBER_900,
                            )
                        ]
                        if anchor not in available_codes
                        else []
                    ),
                    update_progress(
                        [j for j in relevant_jobs if j["status"] != "succeeded"], gen, actor
                    ),
                    ft.Text(anchor_error, color=ft.Colors.RED_700)
                    if anchor_error
                    else ft.Container(),
                    update_feedback,
                    ft.Divider(height=16),
                    *(
                        [
                            ft.Container(
                                content=ft.Text(
                                    str(disc_data.get("error") or disc_data.get("warning")),
                                    color=ft.Colors.RED_700
                                    if disc_data.get("error")
                                    else ft.Colors.AMBER_900,
                                ),
                                bgcolor=ft.Colors.RED_50
                                if disc_data.get("error")
                                else ft.Colors.AMBER_50,
                                padding=10,
                            )
                        ]
                        if disc_data.get("error") or disc_data.get("warning")
                        else []
                    ),
                    ft.Column(controls=rows_controls, spacing=8),
                    section(
                        "适用范围与研究边界",
                        [
                            ft.Text(FACT_LIMITS),
                            ft.Text(
                                "仅支持项目参照清单；金融或行业未知不适用。PB 是市价与每股净资产之比；ROE 是历史净资产收益率。提交取数后使用已证明的上一交易日，财务按本次实际取得的披露资料核查。"
                            ),
                            *(
                                [ft.Text("正在查看指定任务的结果/诊断，并非后来最新资料。")]
                                if job_id
                                else []
                            ),
                        ],
                    ),
                    ft.Button("查看最新资料（不联网更新）", on_click=refresh_result),
                ],
                spacing=8,
            )

        async def render_job(job_id: str, gen: int):
            actor: Actor | None = page_state.get("actor")
            if not actor or not actor.is_valid:
                return await render_login()
            try:
                job = await asyncio.to_thread(get_update_job, actor, job_id, STATE_DIR, APP_MODE)
            except ServiceError:
                return ft.Text("任务不存在或暂不可读取")
            if gen != page_state["generation"] or not actor.is_valid:
                return await render_login()

            status_text = ft.Text("")

            async def open_result(e):
                if (
                    gen != page_state["generation"]
                    or not actor.is_valid
                    or not page_state["connected"]
                ):
                    return
                if job.get("company_code"):
                    await navigate(f"/company/{job['company_code']}")
                    return
                if job["kind"] == "watch":
                    await navigate("/")
                    return
                route = "/discover?" + urlencode({"anchor": job["anchor"], "job": job_id})
                page_state.setdefault("discover_boards", {}).pop(route, None)
                await navigate(route)

            async def return_to_discover(e):
                if (
                    gen != page_state["generation"]
                    or not actor.is_valid
                    or not page_state["connected"]
                ):
                    return
                if job["kind"] == "watch":
                    await navigate("/")
                    return
                page_state["selected_anchor"] = job["anchor"]
                await go_discover(e)

            async def retry(e):
                if (
                    gen != page_state["generation"]
                    or not actor.is_valid
                    or not page_state["connected"]
                ):
                    return
                if job["kind"] == "watch":
                    await return_to_discover(e)
                    return
                dialog_open = True

                async def cancel(e):
                    nonlocal dialog_open
                    if (
                        not confirm_button.disabled
                        and gen == page_state["generation"]
                        and page_state["connected"]
                    ):
                        dialog_open = False
                        page.pop_dialog()

                async def confirm(e):
                    nonlocal dialog_open
                    if not dialog_open:
                        return
                    submitted = await submit_update(
                        job["anchor"], confirm_button, feedback, gen, actor
                    )
                    if submitted:
                        dialog_open = False
                        page.pop_dialog()
                        await navigate(f"/jobs/{submitted['job_id']}")

                feedback = ft.Text("", color=ft.Colors.RED_700)
                confirm_button = ft.TextButton("确认重新扫描", on_click=confirm)
                page.show_dialog(
                    ft.AlertDialog(
                        modal=True,
                        title=ft.Text("确认重新扫描"),
                        content=ft.Column(
                            [
                                ft.Text(
                                    f"参照公司：{job['anchor']}。确认后提交更新任务，由独立worker重新获取资料；范围与目标日按提交时确定。"
                                ),
                                ft.Text(
                                    "相同范围的在途任务会合并；重新获取不保证数据日或数值变化，原任务和已阅基准保留。"
                                ),
                                feedback,
                            ],
                            tight=True,
                        ),
                        actions=[ft.TextButton("取消", on_click=cancel), confirm_button],
                    )
                )

            remove_button = removal_button(
                "清理失败任务",
                "仅从列表移除此失败或中断任务，保留请求编号以防旧请求重新执行；不删除历史快照，也不会重试更新。",
                lambda: dismiss_update_job(actor, job_id, STATE_DIR, APP_MODE),
                gen,
                actor,
            )
            is_watch = job["kind"] == "watch"
            result_button = ft.Button(
                "查看公司资料"
                if job.get("company_code")
                else "查看关注最新资料"
                if is_watch
                else "查看本次结果/诊断",
                on_click=open_result,
            )
            retry_button = ft.Button(
                "回首页重新确认更新" if is_watch else "重新扫描", on_click=retry
            )

            def show_status(current: dict[str, Any]):
                active = current["status"] in ("queued", "running")
                remove_button.visible = current["status"] in ("failed", "interrupted")
                result_button.visible = not active
                retry_button.visible = not active
                status_text.value = (
                    f"状态：{job_status_label(current)} · 数据日：{current['target_date']}"
                    + (" · 服务器已接收，可离开页面稍后回来。" if active else "")
                    + (f" · {current['error_summary']}" if current["error_summary"] else "")
                )

            show_status(job)

            async def poll_job():
                try:
                    while True:
                        await asyncio.sleep(3)
                        if (
                            gen != page_state["generation"]
                            or not actor.is_valid
                            or not page_state["connected"]
                        ):
                            return
                        latest = await asyncio.to_thread(
                            get_update_job, actor, job_id, STATE_DIR, APP_MODE
                        )
                        if (
                            gen != page_state["generation"]
                            or not actor.is_valid
                            or not page_state["connected"]
                        ):
                            return
                        show_status(latest)
                        page.update()
                        if latest["status"] not in ("queued", "running"):
                            return
                except (asyncio.CancelledError, AuthError):
                    return
                except (ServiceError, WorkspaceError):
                    if gen == page_state["generation"] and actor.is_valid:
                        status_text.value = "状态查询失败，请返回首页稍后重试"
                        page.update()

            if job["status"] in ("queued", "running"):
                page_state["job_poll_task"] = asyncio.create_task(poll_job())
            return ft.Column(
                controls=[
                    ft.Text("更新任务", size=18, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        f"固定关注范围（{len(job['codes'])}家）：{', '.join(job['codes'])}"
                        if is_watch
                        else f"参照公司：{job['anchor']}"
                    ),
                    status_text,
                    result_button,
                    retry_button,
                    remove_button,
                    ft.Text(
                        "查看关注最新资料可能包含后续更新，不代表此任务的历史快照。再次更新须回首页确认，按届时未暂停清单和已证明日期冻结新范围；原任务不变。"
                        if is_watch
                        else "重新扫描将在确认后提交更新任务；重新获取成功不代表数据日或数值必然变化。查看本次结果仅阅读历史资料。",
                        size=12,
                    ),
                    ft.Button(
                        "返回我的研究" if is_watch else "返回发现候选", on_click=return_to_discover
                    ),
                ]
            )

        async def render_company(code: str, gen: int):
            actor: Actor | None = page_state.get("actor")
            if not actor or not actor.is_valid:
                return await render_login()

            ctx = await asyncio.to_thread(
                get_company_context, actor, code, STATE_DIR, APP_MODE, include_personal_notes=True
            )
            if gen != page_state["generation"] or not actor.is_valid:
                return await render_login()

            page_state["current_company_code"] = code
            page_state["current_company_revision"] = ctx.get("revision", 0)
            page_state["current_company_updated_at"] = ctx.get("updated_at")
            persisted_baseline = {
                "watch_status": str(ctx.get("watch_status") or "observe"),
                "reason": str(ctx.get("reason") or ""),
                "next_check": str(ctx.get("next_check") or ""),
                "note_url": str(ctx.get("note_url") or ""),
            }
            page_state["current_form_baseline"] = persisted_baseline

            draft = page_state.setdefault("drafts", {}).get(code, {})
            conflict_detected = False
            draft_base_rev = None
            if draft:
                draft_base_rev = draft.get("_base_revision")
                if (draft_base_rev is not None and draft_base_rev != ctx.get("revision", 0)) or (
                    "_base_updated_at" in draft
                    and draft["_base_updated_at"] != ctx.get("updated_at")
                ):
                    conflict_detected = True

            status_val = draft.get("watch_status") or ctx["watch_status"] or "observe"
            reason_val = draft.get("reason") if "reason" in draft else ctx["reason"]
            next_check_val = draft.get("next_check") if "next_check" in draft else ctx["next_check"]
            note_url_val = draft.get("note_url") if "note_url" in draft else (ctx["note_url"] or "")

            status_dd = ft.Dropdown(
                label="关注状态（可选）",
                options=[
                    ft.dropdown.Option("research", "继续研究"),
                    ft.dropdown.Option("observe", "等待证据"),
                    ft.dropdown.Option("paused", "暂不研究（暂停更新）"),
                ],
                value=status_val,
                width=150,
            )
            reason_field = ft.TextField(
                label="研究理由（支持与反对证据，≤1000字）",
                hint_text="业务与估值假设：…\n支持证据及出处：…\n反对证据或关键未知：…",
                value=reason_val,
                multiline=True,
                max_length=1000,
            )
            next_check_field = ft.TextField(
                label="下一步与反证（事件或日期）",
                hint_text="可选。触发：下次财报/某事件；核查：哪项证据；反证：若出现X，重新考虑原判断。",
                multiline=True,
                value=next_check_val,
                max_length=1000,
            )
            note_url_field = ft.TextField(
                label="外部笔记链接 (https://)",
                value=note_url_val,
                max_length=2048,
            )

            def get_current_form() -> dict[str, str]:
                return {
                    "watch_status": str(status_dd.value or "observe"),
                    "reason": str(reason_field.value or ""),
                    "next_check": str(next_check_field.value or ""),
                    "note_url": str(note_url_field.value or ""),
                }

            page_state["current_form_getter"] = get_current_form

            def update_draft(e: Any = None) -> None:
                if gen == page_state["generation"] and actor.is_valid and page_state["connected"]:
                    remember_draft()

            status_dd.on_select = update_draft
            reason_field.on_change = update_draft
            next_check_field.on_change = update_draft
            note_url_field.on_change = update_draft

            feedback_text = ft.Text("", size=13)

            async def on_discard_draft(e: Any = None):
                page_state.setdefault("drafts", {}).pop(code, None)
                await render_current_view()
                page.update()

            async def on_save(e):
                if gen != page_state["generation"] or not page_state["connected"]:
                    return
                if not actor.is_valid:
                    feedback_text.value = "授权已失效，请重新登录"
                    feedback_text.color = ft.Colors.RED_700
                    page.update()
                    return

                if conflict_detected:
                    feedback_text.value = f"版本冲突：当前草稿基于版本 {draft_base_rev}，数据库已更新为版本 {ctx['revision']}，已禁止直接保存"
                    feedback_text.color = ft.Colors.RED_700
                    page.update()
                    return

                try:
                    fields = {
                        "status": status_dd.value,
                        "reason": reason_field.value.strip(),
                        "next_check": next_check_field.value.strip(),
                        "note_url": note_url_field.value.strip() or None,
                    }
                    updated = await asyncio.to_thread(
                        save_watch,
                        actor=actor,
                        code=code,
                        source_run_id=ctx.get("displayed_run_id")
                        or ctx.get("latest_attempt_run_id")
                        or "run_initial",
                        fields=fields,
                        expected_revision=ctx["revision"],
                        expected_updated_at=ctx.get("updated_at"),
                        state_dir=STATE_DIR,
                        mode=APP_MODE,
                    )
                    if (
                        gen != page_state["generation"]
                        or not actor.is_valid
                        or not page_state["connected"]
                    ):
                        return
                    ctx["revision"] = updated["revision"]
                    ctx["is_watched"] = True
                    ctx["updated_at"] = updated["updated_at"]
                    page_state["current_company_updated_at"] = updated["updated_at"]
                    delete_btn.visible = True
                    follow_btn.content = "已关注"
                    follow_btn.disabled = True
                    ack_btn.disabled = bool(
                        already_acknowledged
                        or comparison_pending
                        or ctx.get("has_latest_attempt_gap")
                    )
                    page_state["current_company_revision"] = updated["revision"]
                    page_state["current_form_baseline"] = get_current_form()
                    page_state.setdefault("drafts", {}).pop(code, None)
                    feedback_text.value = "保存成功"
                    feedback_text.color = ft.Colors.GREEN_700
                    ctx["reason"], ctx["next_check"], ctx["note_url"] = (
                        fields["reason"],
                        fields["next_check"],
                        fields["note_url"],
                    )
                    judgment_summary.value = "我的判断：" + labels[fields["status"]]
                    reason_display.value = fields["reason"] or "尚未记录支持理由与反对证据。"
                    next_display.value = "下一步：" + (fields["next_check"] or prompt["next"])
                    note_link.visible = bool(fields["note_url"])
                except (ServiceError, WorkspaceError) as exc:
                    feedback_text.value = f"保存失败: {exc}"
                    feedback_text.color = ft.Colors.RED_700
                except Exception:
                    feedback_text.value = "保存失败: 请稍后重试"
                    feedback_text.color = ft.Colors.RED_700
                page.update()

            page_state["save_current_form"] = on_save
            comparison = ctx.get("comparison") or {
                "can_ack": False,
                "summary": "暂无可用事实对照",
                "items": [],
            }
            comparison_pending = not comparison["can_ack"]
            comparison_summary = ft.Text(comparison["summary"], weight=ft.FontWeight.BOLD)

            def comparison_value(value, label, current):
                if label == "名称风险标记" and value:
                    return {
                        "known_warning": "名称含风险警示",
                        "unknown": "未知（不代表风险解除）",
                        "name_check_clear_other_risks_unknown": "未识别到名称风险警示（不代表无风险）",
                    }.get(value, str(value))
                if label == "上市状态" and value:
                    return {"L": "上市", "D": "退市", "P": "暂停上市"}.get(value, str(value))
                if value is None or value == "":
                    if (
                        current
                        and ctx.get("displayed_kind") == "watch"
                        and label
                        in (
                            "行业",
                            "参照公司",
                        )
                    ):
                        return "固定关注更新不设此项"
                    return "当前资料未记录" if current else "已阅基准未记录"
                if isinstance(value, (tuple, list)):
                    return "、".join(str(v) for v in value) or "无"
                return str(value)

            comparison_rows: list[ft.Control] = []
            comparison_metadata: list[ft.Control] = []
            fact_groups: dict[str, list[ft.Control]] = {
                "指标与公司状态": [],
                "最新财报指标": [],
                "逐年财务资料": [],
                "数据来源与范围": [],
            }
            missing_metadata: list[ft.Control] = []
            for item in comparison["items"]:
                is_changed = bool(item["changed"] and ctx.get("ack_run_id"))
                after = comparison_value(item["after"], item["label"], True)
                values = (
                    f"{comparison_value(item['before'], item['label'], False)} → {after}"
                    if ctx.get("ack_run_id")
                    else after
                )
                line = f"{'【变化】' if is_changed else ''}{item['label']}：{values}"
                label = item["label"]
                is_annual = len(label) > 4 and label[:4].isdigit() and label[4] == "年"
                group = (
                    "逐年财务资料"
                    if is_annual
                    else "指标与公司状态"
                    if label
                    in ("估值日", "PB（倍）", "ROE三年均值（%）", "名称风险标记", "上市状态")
                    else "最新财报指标"
                    if label.startswith("最新财报")
                    or label in {f"{name}（{unit}）" for name, unit in REPORT_FIELDS.values()}
                    else "数据来源与范围"
                )
                if is_changed:
                    changes = comparison_metadata if group == "数据来源与范围" else comparison_rows
                    changes.append(
                        ft.Container(
                            content=ft.Text(
                                line, size=13, weight=ft.FontWeight.W_600, color=ft.Colors.AMBER_900
                            ),
                            bgcolor=ft.Colors.AMBER_50,
                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                            border_radius=4,
                        )
                    )
                status = "变化" if is_changed else "未变" if ctx.get("ack_run_id") else "首次待阅"
                field_controls: list[ft.Control] = [
                    ft.Text(f"{label} · {status}", size=13, weight=ft.FontWeight.W_600),
                    ft.Text(f"当前资料：{after}", size=14, selectable=True),
                ]
                if is_changed:
                    field_controls.append(
                        ft.Text(
                            f"上次已阅：{comparison_value(item['before'], label, False)}",
                            size=13,
                            color=ft.Colors.GREY_800,
                            selectable=True,
                        )
                    )
                field = ft.Container(
                    content=ft.Column(field_controls, spacing=6),
                    bgcolor=ft.Colors.AMBER_50 if is_changed else ft.Colors.GREY_50,
                    padding=12,
                    border_radius=8,
                )
                if (
                    is_annual
                    and label.endswith(("报告类型", "修订标记"))
                    and (item["before"] in (None, "") and item["after"] in (None, ""))
                ):
                    missing_metadata.append(field)
                elif label == "本次范围成员":
                    fact_groups[group].append(
                        ft.ExpansionTile(
                            title=f"本次范围成员 · {status}",
                            controls=[field],
                        )
                    )
                else:
                    fact_groups[group].append(field)

            if comparison_metadata:
                comparison_rows.append(
                    ft.ExpansionTile(title="来源与范围变化", controls=comparison_metadata)
                )
            full_comparison: list[ft.Control] = []
            for title, fields in fact_groups.items():
                if fields:
                    full_comparison.extend(
                        [
                            ft.Text(title, size=16, weight=ft.FontWeight.BOLD),
                            ft.Column(
                                fields,
                                spacing=8,
                                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                            ),
                        ]
                    )
            if missing_metadata:
                full_comparison.append(
                    ft.ExpansionTile(
                        title=f"未记录的补充字段（{len(missing_metadata)}项）",
                        controls=missing_metadata,
                    )
                )

            comparison_key = (page_state["route"], "full_comparison")
            comparison_body = ft.Column(
                [
                    ft.Text(
                        "未记录表示快照中没有该字段，不能据此判断接口是否提供；核心资料获取失败会在页面顶部单独警示。",
                        size=12,
                    ),
                    *full_comparison,
                ],
                spacing=16,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                visible=page_state["expanded_sections"].get(comparison_key, False),
            )

            async def toggle_comparison(e):
                if gen == page_state["generation"] and actor.is_valid and page_state["connected"]:
                    comparison_body.visible = not comparison_body.visible
                    page_state["expanded_sections"][comparison_key] = comparison_body.visible
                    page.update()

            ack_feedback = ft.Text("", size=13, key="review-feedback")

            async def on_ack(e):
                if gen != page_state["generation"] or not page_state["connected"]:
                    return
                if comparison_pending:
                    ack_feedback.value = comparison["summary"]
                    page.update()
                    return
                if not actor.is_valid:
                    ack_feedback.value = "授权已失效，请重新登录"
                    ack_feedback.color = ft.Colors.RED_700
                    page.update()
                    return

                if conflict_detected:
                    ack_feedback.value = f"版本冲突：数据库已更新为版本 {ctx['revision']}，请先刷新或放弃草稿载入最新"
                    ack_feedback.color = ft.Colors.RED_700
                    page.update()
                    return
                if ctx.get("has_latest_attempt_gap"):
                    ack_feedback.value = (
                        "最新快照异常或本次尝试存在数据缺口，不可将旧资料标记为本次已阅"
                    )
                    ack_feedback.color = ft.Colors.RED_700
                    page.update()
                    return

                try:
                    disp_run = ctx.get("displayed_run_id")
                    if not disp_run:
                        ack_feedback.value = "暂无可确认的运行资料"
                        ack_feedback.color = ft.Colors.RED_700
                    else:
                        updated = await asyncio.to_thread(
                            mark_seen,
                            actor=actor,
                            code=code,
                            displayed_run_id=disp_run,
                            expected_revision=ctx["revision"],
                            expected_updated_at=ctx.get("updated_at"),
                            state_dir=STATE_DIR,
                            mode=APP_MODE,
                        )
                        if (
                            gen != page_state["generation"]
                            or not actor.is_valid
                            or not page_state["connected"]
                        ):
                            return
                        ctx["revision"] = updated["revision"]
                        ctx["updated_at"] = updated["updated_at"]
                        page_state["current_company_updated_at"] = updated["updated_at"]
                        page_state["current_company_revision"] = updated["revision"]
                        ack_feedback.value = f"已标记已阅 (版本: {updated['revision']})"
                        ack_feedback.color = ft.Colors.GREEN_700
                        comparison_summary.value = "本页所示资料已阅；下次打开将以此作为对照基准。"
                        ack_btn.disabled = True
                except (ServiceError, WorkspaceError) as exc:
                    ack_feedback.value = f"标记已阅失败: {exc}"
                    ack_feedback.color = ft.Colors.RED_700
                except Exception:
                    ack_feedback.value = "标记已阅失败: 请稍后重试"
                    ack_feedback.color = ft.Colors.RED_700
                page.update()

            usable = ctx.get("usable_fact") or {}
            annual_roes = usable.get("annual_roes") or []
            roe_rows = [
                ft.Text(
                    f"{r.get('year')}年: ROE {r.get('roe')}% (公告日: {r.get('ann_date') or '资料未记录'})",
                    size=13,
                )
                for r in annual_roes
            ] or [ft.Text("年度ROE暂无可用资料，不能判断趋势。", color=ft.Colors.RED_900)]
            financial_status = {
                "ok": "财务资料可用",
                "failed": "财务获取失败",
                "missing": "财务资料缺失",
                "conflict": "财务资料存在冲突",
            }.get(str(usable.get("financial_status")), "暂无可用财务核查记录")

            delete_btn = removal_button(
                "删除个人研究记录",
                f"确认删除 {ctx['name']}（{code}）的个人研究记录？理由、下一步、笔记链接、已阅状态和本页草稿将被移除，不能撤销。历史扫描及外部笔记原文不受影响；再次加入会从空记录开始。",
                lambda: delete_watch(
                    actor, code, ctx["revision"], ctx["updated_at"], STATE_DIR, APP_MODE
                ),
                gen,
                actor,
                code=code,
            )
            delete_btn.visible = ctx["is_watched"]
            already_acknowledged = bool(
                ctx.get("ack_run_id") and ctx["ack_run_id"] == ctx.get("displayed_run_id")
            )
            save_btn = ft.Button(
                "保存笔记与状态", height=48, on_click=on_save, disabled=conflict_detected
            )
            ack_btn = ft.Button(
                "已标记本次已阅" if already_acknowledged else "标记本次变化已阅",
                key="mark-reviewed",
                height=48,
                on_click=on_ack,
                disabled=already_acknowledged
                or not ctx["is_watched"]
                or comparison_pending
                or conflict_detected
                or ctx.get("has_latest_attempt_gap", False),
            )

            notes_key = (page_state["route"], "optional_notes")
            notes_expanded = bool(draft) or page_state["expanded_sections"].get(notes_key, False)

            async def toggle_notes(e):
                nonlocal notes_expanded
                if (
                    gen != page_state["generation"]
                    or not actor.is_valid
                    or not page_state["connected"]
                ):
                    return
                notes_expanded = not notes_expanded
                notes_button.content = ("收起" if notes_expanded else "展开") + "可选笔记与状态"
                page_state["expanded_sections"][notes_key] = notes_expanded
                for control in form_controls[1:]:
                    control.visible = notes_expanded and (
                        control is not delete_btn or ctx["is_watched"]
                    )
                page.update()

            notes_button = ft.Button(
                ("收起" if notes_expanded else "展开") + "可选笔记与状态",
                on_click=toggle_notes,
            )
            form_controls: list[ft.Control] = [notes_button]
            if conflict_detected:
                conflict_banner = ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(
                                f"⚠️ 版本冲突提示：此记录已被外部更新（最新版本: {ctx['revision']}，草稿基于版本: {draft_base_rev}）。",
                                weight=ft.FontWeight.BOLD,
                                color=ft.Colors.RED_900,
                            ),
                            ft.Text(
                                "为防止覆盖最新数据，已禁止直接保存。您可以核对/复制草稿内容，然后点击下方按钮载入最新版本：",
                                size=13,
                                color=ft.Colors.RED_800,
                            ),
                            ft.Button("放弃草稿并载入最新版本", on_click=on_discard_draft),
                        ],
                        spacing=6,
                    ),
                    bgcolor="#FEF2F2",
                    border=ft.Border.all(1, "#FECACA"),
                    border_radius=8,
                    padding=12,
                )
                form_controls.append(conflict_banner)

            form_controls.extend(
                [
                    status_dd,
                    reason_field,
                    next_check_field,
                    note_url_field,
                    ft.Row(
                        controls=[
                            save_btn,
                        ],
                        spacing=8,
                        wrap=True,
                    ),
                    feedback_text,
                    delete_btn,
                    *(
                        [
                            ft.Text(
                                comparison["summary"],
                                color=ft.Colors.AMBER_900,
                            )
                        ]
                        if comparison_pending
                        else []
                    ),
                ]
            )

            for control in form_controls[1:]:
                control.visible = notes_expanded and (
                    control is not delete_btn or ctx["is_watched"]
                )

            follow_feedback = ft.Text("关注不要求写笔记，也不自动标记已阅。", size=12)
            follow_btn = ft.Button(
                "已关注" if ctx["is_watched"] else "关注", height=48, disabled=ctx["is_watched"]
            )

            async def follow(e):
                if (
                    gen != page_state["generation"]
                    or not actor.is_valid
                    or not page_state["connected"]
                ):
                    return
                follow_btn.disabled = True
                page.update()
                try:
                    await asyncio.to_thread(
                        save_watch,
                        actor,
                        code,
                        ctx.get("displayed_run_id")
                        or ctx.get("latest_attempt_run_id")
                        or "run_initial",
                        {"status": "observe"},
                        0,
                        STATE_DIR,
                        APP_MODE,
                    )
                    if (
                        gen == page_state["generation"]
                        and actor.is_valid
                        and page_state["connected"]
                    ):
                        await navigate(page_state["route"])
                except (ServiceError, WorkspaceError, AuthError):
                    if (
                        gen == page_state["generation"]
                        and actor.is_valid
                        and page_state["connected"]
                    ):
                        follow_feedback.value = "关注未确认，请重查或重试；重复关注不会覆盖原记录。"
                        follow_btn.disabled = False
                        page.update()

            follow_btn.on_click = follow
            insights = ctx.get("fact_insights") or {}
            peer_rank = ctx.get("peer_rank")
            identity_notice = (
                "最新基础信息：退市或暂停上市，请核查；个人记录不会删除。"
                if ctx.get("latest_listing_status") in ("D", "P")
                else ""
            )
            if ctx.get("latest_risk_status") == "known_warning":
                identity_notice += " 名称含ST风险警示，仍可查看可得事实，不代表风险已解除。"
            prompt = ctx.get("research_prompt") or research_prompt(usable)
            labels = {
                "research": "继续研究",
                "observe": "等待证据",
                "paused": "暂不研究（暂停更新）",
            }
            judgment_summary = ft.Text(
                "我的判断：" + labels.get(ctx.get("watch_status") or "", "尚未记录"),
                size=16,
                weight=ft.FontWeight.BOLD,
            )
            reason_display = ft.Text(
                ctx.get("reason") or "尚未记录支持理由与反对证据。", size=14, selectable=True
            )
            next_display = ft.Text(
                "下一步：" + (ctx.get("next_check") or prompt["next"]), size=14, selectable=True
            )
            company_feedback = ft.Text("", size=13)
            company_update = ft.Button("更新这家公司", height=48)

            async def update_company(e):
                job = await submit_update(
                    None, company_update, company_feedback, gen, actor, company_code=code
                )
                if job:
                    remember_draft()
                    await navigate(page_state["route"])

            company_update.on_click = update_company

            async def open_url(url: str):
                parsed = urlparse(url)
                if (
                    gen == page_state["generation"]
                    and actor.is_valid
                    and page_state["connected"]
                    and parsed.scheme == "https"
                    and parsed.netloc
                    and not parsed.username
                    and not parsed.password
                ):
                    await ft.UrlLauncher().launch_url(url, web_only_window_name="_blank")

            async def open_disclosures(e):
                await open_url(
                    "https://www.cninfo.com.cn/new/fulltextSearch?"
                    + urlencode({"keyWord": code.split(".")[0]})
                )

            async def open_note(e):
                await open_url(ctx.get("note_url") or "")

            note_link = ft.Button(
                "打开研究笔记", on_click=open_note, visible=bool(ctx.get("note_url"))
            )
            report = ctx.get("research_report") or {}
            report_controls: list[ft.Control] = []
            if report.get("period"):
                report_controls.append(
                    ft.Text(
                        f"本次接口最新报告期：{report['period']} · 公告：{report.get('ann_date') or '未知'}",
                        weight=ft.FontWeight.BOLD,
                    )
                )
            for key, (label, unit) in REPORT_FIELDS.items():
                value = (report.get("metrics") or {}).get(key)
                report_controls.append(
                    ft.Text(
                        f"{label}：{fmt_number(value)} {unit}"
                        if value is not None
                        else f"{label}：尚未取得"
                    )
                )
            report_controls.extend(
                [
                    ft.Text(
                        report.get("error")
                        or (
                            "最新财报字段有缺口，不能用旧值补齐。"
                            if report.get("status") == "partial"
                            else "当前快照未含最新财报证据，可主动更新这家公司。"
                            if not report
                            else "以上为接口事实，尚需与公告原文核对。"
                        ),
                        size=13,
                    ),
                    ft.Text(
                        f"来源：{report.get('source') or '未取得'} · 取得：{report.get('acquired_at') or '未取得'}",
                        size=12,
                    ),
                    ft.Text(
                        "同比为报告期累计口径；每股经营现金流也是累计值，不直接与全年值比较。资产负债率不等于有息负债率。",
                        size=12,
                    ),
                    ft.Button("查公司公告与财报原文", on_click=open_disclosures),
                    ft.Text(
                        "公告入口为按代码检索，须核对证券身份、报告期及具体原文；不表示系统已阅读公告。",
                        size=12,
                    ),
                ]
            )
            recent_jobs = await asyncio.to_thread(list_update_jobs, actor, STATE_DIR, APP_MODE)
            if gen != page_state["generation"] or not actor.is_valid:
                return await render_login()
            relevant_jobs = [
                j for j in recent_jobs if j["kind"] == "watch" and code in j.get("codes", [])
            ]
            return ft.Column(
                [
                    ft.Row(
                        [
                            ft.IconButton(ft.Icons.ARROW_BACK, tooltip="返回", on_click=go_back),
                            ft.Text(
                                f"{ctx['name'] or code} ({code})",
                                size=18,
                                weight=ft.FontWeight.BOLD,
                                expand=True,
                            ),
                        ]
                    ),
                    *(
                        [
                            ft.Container(
                                content=ft.Text(
                                    f"{ctx.get('latest_attempt_error') or '本次尝试失败/数据缺口'}（{ctx.get('latest_attempt_date') or '日期未知'}）；当前仅展示旧可用事实（{ctx.get('usable_valuation_date') or '暂无'}），不可标记本次变化已阅。",
                                    color=ft.Colors.RED_900,
                                ),
                                bgcolor="#FEF2F2",
                                border=ft.Border.all(1, "#FECACA"),
                                border_radius=8,
                                padding=12,
                            )
                        ]
                        if ctx.get("has_latest_attempt_gap")
                        else []
                    ),
                    *(
                        [ft.Text(identity_notice, color=ft.Colors.RED_900)]
                        if identity_notice
                        else []
                    ),
                    ft.Card(
                        semantic_container=False,
                        content=ft.Container(
                            padding=16,
                            bgcolor=COLOR_SURFACE,
                            border=ft.Border.all(1, COLOR_BORDER),
                            border_radius=8,
                            content=ft.Column(
                                [
                                    judgment_summary,
                                    reason_display,
                                    ft.Text(
                                        "研究提示：" + prompt["action"], weight=ft.FontWeight.BOLD
                                    ),
                                    ft.Text(prompt["why"], size=14, color="#334155"),
                                    next_display,
                                    ft.Row([follow_btn, company_update], wrap=True, spacing=8),
                                    follow_feedback,
                                    company_feedback,
                                    update_progress(relevant_jobs, gen, actor),
                                    ft.Column(form_controls, key="research-form", spacing=10),
                                    note_link,
                                ],
                                spacing=10,
                            ),
                        ),
                    ),
                    ft.Card(
                        semantic_container=False,
                        content=ft.Container(
                            padding=16,
                            bgcolor=COLOR_SURFACE,
                            border=ft.Border.all(1, COLOR_BORDER),
                            border_radius=8,
                            content=ft.Column(
                                [
                                    ft.Text(
                                        "本次变化与原判断",
                                        size=16,
                                        weight=ft.FontWeight.BOLD,
                                        color=COLOR_TEXT_PRIMARY,
                                    ),
                                    ft.Text(
                                        "上次已阅 → 当前资料"
                                        if ctx.get("ack_run_id")
                                        else "当前资料（首次待阅，无已阅基准）",
                                        size=13,
                                        color=COLOR_TEXT_SECONDARY,
                                    ),
                                    comparison_summary,
                                    *comparison_rows,
                                    ft.Text(
                                        "请对照上方原研究理由：支持是否增强，反对证据是否出现，还是仅估值或资料口径变化？",
                                        size=13,
                                        color="#334155",
                                    ),
                                    ack_btn,
                                    ack_feedback,
                                    ft.Text(
                                        "已阅确认界面所示事实并更新对照基准，不代表已核验公告原文或作出投资判断；保存笔记不会自动已阅。",
                                        size=12,
                                        color=COLOR_TEXT_MUTED,
                                    ),
                                ],
                                spacing=10,
                            ),
                        ),
                    ),
                    ft.ExpansionTile(title="最新财报证据与原文", controls=report_controls),
                    ft.ExpansionTile(
                        title="业务、风险与估值：五个核查问题",
                        controls=[
                            ft.Text(
                                "1. 靠什么赚钱？查年报主营业务、收入构成与经营讨论，确认同业业务可比。"
                            ),
                            ft.Text(
                                "2. 利润是否可靠？查最新中期财报、扣非利润与现金流量表，核对利润和现金差异。"
                            ),
                            ft.Text(
                                "3. 财务是否脆弱？查债务期限、受限资金、应收与存货减值、审计意见。"
                            ),
                            ft.Text(
                                "4. 价格要求什么条件？写明适用估值方法、盈利或现金流假设及悲观情景；PB排名不能替代估值。"
                            ),
                            ft.Text(
                                "5. 什么会推翻判断？在下一步记录一条可核查事件或日期，以及需要改变判断的证据。"
                            ),
                            ft.Text(
                                "业务、审计和估值假设由你核实并记录；本工具不自动生成目标价、买卖时点或仓位。",
                                size=13,
                            ),
                        ],
                    ),
                    ft.ExpansionTile(
                        title="PB、历史ROE与入选依据",
                        controls=[
                            ft.Text(
                                "公司资料事实"
                                if ctx.get("displayed_kind") == "watch"
                                else "公司筛选事实",
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(
                                f"PB: {fmt_number(usable.get('pb'))} (估值日: {ctx.get('usable_valuation_date') or '暂无可用日期'})"
                            ),
                            ft.Text(f"ROE三年均值: {fmt_number(usable.get('roe_mean'))}%"),
                            *roe_rows,
                            ft.Text(insights.get("pb_comparison", "暂无同口径同业比较")),
                            ft.Text(insights.get("roe_trend", "ROE趋势：资料有缺口，暂不判断")),
                            ft.Text(ctx.get("entry_reason") or "尚未记录入选来源", size=13),
                            ft.Text(
                                f"最近同业名次：{peer_rank['position']}（参照{peer_rank['anchor']}，估值日{peer_rank['valuation_date']}）；固定关注更新不改变此历史名次。"
                                if peer_rank
                                else "暂无可用同业名次；固定关注更新不生成名次。",
                                size=12,
                            ),
                            ft.Text("固定关注更新不重新排名；行业标签不能证明业务可比。", size=12),
                            ft.Text(FACT_LIMITS, size=13),
                            ft.Text(
                                f"核查状态：{financial_status}；核查时间：{usable.get('financial_checked_at') or '资料未记录'}",
                                size=12,
                            ),
                        ],
                    ),
                    ft.Button("查看全部字段与来源", on_click=toggle_comparison),
                    comparison_body,
                ],
                spacing=12,
            )

        async def render_settings():
            actor: Actor | None = page_state.get("actor")
            return ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.IconButton(ft.Icons.ARROW_BACK, tooltip="返回", on_click=go_home),
                            ft.Text(
                                "账户与运行信息",
                                size=18,
                                weight=ft.FontWeight.BOLD,
                                color=COLOR_TEXT_PRIMARY,
                            ),
                        ]
                    ),
                    ft.Text(f"当前模式: {APP_MODE}", size=14, color=COLOR_TEXT_SECONDARY),
                    ft.Text(f"数据目录: {STATE_DIR}", size=14, color=COLOR_TEXT_SECONDARY),
                    ft.Text(
                        f"用户身份: {actor.user_id if actor else '未登录'}",
                        size=14,
                        color=COLOR_TEXT_SECONDARY,
                    ),
                    ft.Button("退出登录", icon=ft.Icons.LOGOUT, on_click=on_logout)
                    if actor
                    else ft.Container(),
                ],
                spacing=12,
            )

        async def render_current_view():
            gen = page_state["generation"]
            actor: Actor | None = page_state.get("actor")
            if (not actor or not actor.is_valid) and page_state["route"] != "/login":
                page_state["route"] = "/login"
            route = urlparse(page_state["route"]).path
            nav_bar.selected_index = (
                1
                if route == "/discover"
                or route.startswith("/jobs/")
                or (
                    route.startswith("/company/")
                    and page_state["company_return"].startswith("/discover")
                )
                else 0
            )
            if not page_state["connected"]:
                return
            if content_container.content is not loading_content:
                content_container.content = loading_content
                page.update()
            try:
                if route == "/login":
                    content = await render_login()
                elif route == "/":
                    content = await render_home(gen)
                elif route == "/discover":
                    content = await render_discover(gen)
                elif route.startswith("/jobs/"):
                    content = await render_job(route.rsplit("/", 1)[-1], gen)
                elif route.startswith("/company/"):
                    content = await render_company(route.split("/")[-1], gen)
                elif route == "/settings":
                    content = await render_settings()
                else:
                    content = await render_login()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Page read unavailable (%s)", type(exc).__name__
                )

                async def retry_read(e):
                    if (
                        gen == page_state["generation"]
                        and actor
                        and actor.is_valid
                        and page_state["connected"]
                    ):
                        await render_current_view()
                        page.update()

                content = ft.Column(
                    [
                        ft.Text("暂时无法读取资料；已保存内容不会因此删除。"),
                        ft.Text(
                            "可重新读取或返回我的研究查看最近任务；只读取资料，不会提交或重放更新。"
                        ),
                        ft.Button("重新读取", on_click=retry_read),
                    ]
                )

            if gen == page_state["generation"] and page_state["connected"]:
                if route != "/login" and (not actor or not actor.is_valid):
                    page_state["route"] = "/login"
                    content = await render_login()
                content_container.content = content

        async def on_nav_change(e):
            if e.control.selected_index == 0:
                await navigate("/")
            elif e.control.selected_index == 1:
                await go_discover(e)

        nav_bar = ft.NavigationBar(
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.LIST, label="我的研究"),
                ft.NavigationBarDestination(icon=ft.Icons.SEARCH, label="发现候选"),
            ],
            selected_index=0,
            on_change=on_nav_change,
        )

        header = ft.Container(
            padding=ft.Padding.only(left=8, right=8, top=4, bottom=8),
            border=ft.Border(bottom=ft.BorderSide(1, COLOR_BORDER)),
            content=ft.Row(
                controls=[
                    ft.Text(
                        "基本面研究助手",
                        size=16,
                        weight=ft.FontWeight.BOLD,
                        color=COLOR_TEXT_PRIMARY,
                    ),
                    ft.Container(
                        content=ft.Text(
                            "DEMO" if APP_MODE == "demo" else "PROD",
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color="#C2410C" if APP_MODE == "demo" else "#15803D",
                        ),
                        bgcolor="#FFF7ED" if APP_MODE == "demo" else "#F0FDF4",
                        border=ft.Border.all(1, "#FDBA74" if APP_MODE == "demo" else "#86EFAC"),
                        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                        border_radius=12,
                    ),
                    ft.IconButton(
                        ft.Icons.SETTINGS, tooltip="账户与运行信息", on_click=go_settings
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

        main_column = ft.Container(
            width=640,
            content=ft.Column(
                controls=[
                    header,
                    content_container,
                ],
                expand=True,
            ),
        )

        page.navigation_bar = nav_bar
        page.add(
            ft.ResponsiveRow(
                controls=[
                    ft.Column(
                        controls=[main_column],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    )
                ]
            )
        )
        await render_current_view()
        page.update()

        async def session_watchdog():
            try:
                while True:
                    await asyncio.sleep(5)
                    actor: Actor | None = page_state.get("actor")
                    if actor and not actor.is_valid:
                        page_state["generation"] += 1
                        page_state.setdefault("drafts", {}).clear()
                        page_state["current_form_getter"] = None
                        page_state["current_company_code"] = None
                        page_state["current_form_baseline"] = None
                        actor.revoke()
                        page_state["actor"] = None
                        if page_state["route"] != "/login":
                            await navigate("/login")
            except asyncio.CancelledError:
                pass

        watchdog_task: asyncio.Task[None] | None = asyncio.create_task(session_watchdog())

        async def on_disconnect(e):
            nonlocal watchdog_task
            page_state["connected"] = False
            poll = page_state.get("job_poll_task")
            if poll is not None:
                poll.cancel()
                page_state["job_poll_task"] = None
            page_state["generation"] += 1
            try:
                remember_draft()
            except Exception:
                pass
            if watchdog_task is not None:
                watchdog_task.cancel()
                watchdog_task = None

        page.on_disconnect = on_disconnect

        async def on_connect(e):
            nonlocal watchdog_task
            page_state["connected"] = True
            page.pop_dialog()  # Old confirmation callbacks have an expired generation.
            if watchdog_task is None or watchdog_task.done():
                watchdog_task = asyncio.create_task(session_watchdog())
            actor: Actor | None = page_state.get("actor")
            if actor and actor.is_valid:
                await render_current_view()
                page.update()
            else:
                page_state.setdefault("drafts", {}).clear()
                page_state["current_form_getter"] = None
                page_state["current_company_code"] = None
                page_state["current_company_revision"] = None
                page_state["current_form_baseline"] = None
                if APP_MODE == "demo":
                    page_state["actor"] = create_demo_actor()
                    await render_current_view()
                    page.update()
                else:
                    if actor:
                        actor.revoke()
                    page_state["actor"] = None
                    if page_state["route"] != "/login":
                        await navigate("/login")
                    else:
                        await render_current_view()
                        page.update()

        page.on_connect = on_connect

        async def on_close(e):
            nonlocal watchdog_task
            page_state["connected"] = False
            poll = page_state.get("job_poll_task")
            if poll is not None:
                poll.cancel()
                page_state["job_poll_task"] = None
            page_state["generation"] += 1
            page_state.setdefault("drafts", {}).clear()
            page_state["current_form_getter"] = None
            page_state["current_company_code"] = None
            page_state["current_company_revision"] = None
            page_state["current_form_baseline"] = None
            actor: Actor | None = page_state.get("actor")
            if actor:
                actor.revoke()
            page_state["actor"] = None
            if watchdog_task is not None:
                watchdog_task.cancel()
                watchdog_task = None

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
        # Explicitly disable /upload
        if (path.startswith("/upload") or path == "/upload") and scope["type"] == "http":
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
