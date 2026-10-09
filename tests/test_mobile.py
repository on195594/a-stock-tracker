"""Mobile end-to-end browser smoke test with isolated temporary demo workspace."""

import argparse
import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from a_stock_tracker.workspace import import_snapshot

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCREENSHOT_PATH = Path(f"/tmp/mobile_test_failure_{time.time_ns()}.png")

# Ensure localhost traffic does not hit corporate or sandbox proxies
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"


def wait_for_server(url: str, timeout_sec: int = 15) -> bool:
    start = time.time()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.time() - start < timeout_sec:
        try:
            with opener.open(url, timeout=1) as response:
                if response.status in (200, 404, 503):
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code in (200, 404, 503):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def click_settled(page, control):
    """Wait for Flutter's semantic scroll geometry; never force a hidden hit target."""
    control.wait_for(state="visible")
    # DOM scrolling can move Flutter's invisible semantics without moving the painted
    # control. Use a real wheel event so the hit target and canvas stay together.
    box = control.bounding_box()
    footer = page.get_by_role("tablist").bounding_box()
    assert box and footer
    page.mouse.move(page.viewport_size["width"] / 2, footer["y"] / 2)
    page.mouse.wheel(0, box["y"] + box["height"] / 2 - footer["y"] / 2)
    handle = control.element_handle()
    page.wait_for_function(
        """el => {
        const r = el.getBoundingClientRect();
        const geometry = [r.x, r.y, r.width, r.height].join(',');
        if (el._testGeometry !== geometry) {
            el._testGeometry = geometry;
            el._testStableSince = performance.now();
        }
        return performance.now() - el._testStableSince >= 250;
    }""",
        arg=handle,
        timeout=5000,
    )
    box = control.bounding_box()
    footer = page.get_by_role("tablist").bounding_box()
    assert box and footer
    if box["y"] < 0 or box["y"] + box["height"] > footer["y"]:
        page.mouse.move(page.viewport_size["width"] / 2, footer["y"] / 2)
        page.mouse.wheel(0, box["y"] + box["height"] / 2 - footer["y"] / 2)
        page.wait_for_function(
            """el => {
            const r = el.getBoundingClientRect();
            const bottom = document.querySelector('[role=tablist]').getBoundingClientRect().top;
            return r.top >= 0 && r.bottom <= bottom;
        }""",
            arg=handle,
            timeout=5000,
        )
    is_input = control.evaluate("el => el.matches('input, textarea')")
    control.click()
    if is_input:
        page.wait_for_function(
            """el => {
                const r = el.getBoundingClientRect();
                const state = [document.activeElement === el, r.x, r.y, r.width, r.height].join(',');
                if (el._focusState !== state) {
                    el._focusState = state;
                    el._focusSince = performance.now();
                }
                return document.activeElement === el && performance.now() - el._focusSince >= 350;
            }""",
            arg=control.element_handle(),
            timeout=5000,
        )


def ensure_routine_expanded(page):
    from playwright.sync_api import expect

    expand = page.get_by_role("button", name="查看其余研究（含估值与日期变化）", exact=True)
    collapse = page.get_by_role("button", name="收起其余研究（含估值与日期变化）", exact=True)
    expect(expand.or_(collapse)).to_be_visible()
    if expand.is_visible():
        click_settled(page, expand)
    expect(collapse).to_be_visible()


def check_reliability(page, base_url, state_dir, enable_accessibility):
    """Small repeatable real-input loop, without the full task/snapshot scenario."""
    from playwright.sync_api import expect

    from a_stock_tracker.auth import create_demo_actor
    from a_stock_tracker.services import save_watch

    run = import_snapshot(state_dir, FIXTURES_DIR / "peer_complete_v1.json", "demo")
    save_watch(create_demo_actor(), "600001.SH", run, {}, 0, state_dir, "demo")
    sockets = []

    def connect(ws):
        assert urlparse(ws.url).hostname == "127.0.0.1"
        sockets.append((ws, ws.connect_to_server()))

    page.route_web_socket("**/*", connect)
    page.goto(f"{base_url}/")
    enable_accessibility()
    previous = ("", "")
    for width in (360, 390, 430):
        page.set_viewport_size({"width": width, "height": 844})
        samples = []
        for attempt in range(1, 11):
            print(f"Reliability {width}px #{attempt}: open/input/save/reconnect/return", flush=True)
            start = time.perf_counter()
            page.get_by_role("button", name="查看详情", exact=True).first.click()
            expect(page).to_have_url(re.compile(r"/company/600001.SH$"))
            notes = page.get_by_role("button", name=re.compile("^(展开|收起)可选笔记"))
            if notes.inner_text().startswith("展开"):
                notes.click()
            reason = page.get_by_role("textbox", name=re.compile("研究理由"))
            next_check = page.get_by_role("textbox", name="下一步与反证（事件或日期）", exact=False)
            reason_value, next_value = f"合成理由{width}-{attempt}", f"核查原文{width}-{attempt}"
            for field, value, old_value in (
                (reason, reason_value, previous[0]),
                (next_check, next_value, previous[1]),
            ):
                click_settled(page, field)
                expect(field).to_be_focused()
                # Flutter populates the editor after focus; don't select its transient empty value.
                expect(field).to_have_value(old_value)
                page.keyboard.press("ControlOrMeta+A")
                page.wait_for_function(
                    "el => el.selectionStart === 0 && el.selectionEnd === el.value.length",
                    arg=field.element_handle(),
                )
                page.keyboard.press("Backspace")
                expect(field).to_have_value("")
                page.keyboard.type(value)
                expect(field).to_have_value(value)
            save = page.get_by_role("button", name="保存笔记与状态", exact=True)
            click_settled(page, save)
            expect(page.get_by_text("保存成功", exact=False)).to_be_visible()
            with sqlite3.connect(state_dir / "workspace.sqlite3") as conn:
                assert conn.execute(
                    "SELECT reason,next_check FROM watch_items WHERE code='600001.SH'"
                ).fetchone() == (reason_value, next_value)
                assert conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0] == 0

            count = len(sockets)
            old_editor = reason.element_handle()
            client, server = sockets[-1]
            server.close(code=1012, reason="synthetic reconnect")
            client.close(code=1012, reason="synthetic reconnect")
            deadline = time.monotonic() + 15
            while len(sockets) == count and time.monotonic() < deadline:
                page.wait_for_timeout(50)  # Poll an observed connection, not a layout delay.
            assert len(sockets) > count, "WebSocket did not reconnect"
            page.wait_for_function("el => !el.isConnected", arg=old_editor)
            expect(notes).to_be_visible()
            if notes.inner_text().startswith("展开"):
                click_settled(page, notes)
            click_settled(page, reason)
            expect(reason).to_have_value(reason_value)
            click_settled(page, next_check)
            expect(next_check).to_have_value(next_value)
            page.get_by_role("tab", name="我的研究", exact=False).click()
            expect(page).to_have_url(f"{base_url}/")
            expect(
                page.get_by_text(next_value, exact=False)
                .or_(page.get_by_role("group", name=re.compile(re.escape(next_value))))
                .first
            ).to_be_visible()
            previous = (reason_value, next_value)
            samples.append(time.perf_counter() - start)
        print(
            f"Reliability {width}px: 10/10; full-loop seconds median={statistics.median(samples):.2f}, max={max(samples):.2f}",
            flush=True,
        )


def main(*, reliability: bool = False) -> int:
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError:
        print(
            "ERROR: playwright is not installed. Run `make setup` and `python3 -m playwright install chromium` first.",
            file=sys.stderr,
        )
        return 1

    temp_dir = tempfile.TemporaryDirectory(prefix="stock_tracker_mobile_")
    state_dir = Path(temp_dir.name)
    port = 8555

    print(f"Setting up isolated demo workspace in {state_dir}...")
    setup_cmd = [
        sys.executable,
        "-m",
        "a_stock_tracker.manage",
        "init",
        "--state-dir",
        str(state_dir),
        "--journal-mode",
        "DELETE",
    ]
    res = subprocess.run(setup_cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        print(f"Failed to setup demo workspace: {res.stderr}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env.update(
        {
            "APP_MODE": "demo",
            "STATE_DIR": str(state_dir),
            "PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "FLET_WEB_NO_CDN": "true",
            "no_proxy": "127.0.0.1,localhost",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )

    server_proc = subprocess.Popen(
        [sys.executable, "-m", "a_stock_tracker.app"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    worker_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "a_stock_tracker.worker",
            "--mode",
            "demo",
            "--state-dir",
            str(state_dir),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        base_url = f"http://127.0.0.1:{port}"
        if not wait_for_server(base_url, timeout_sec=10):
            err_output = ""
            if server_proc.poll() is not None and server_proc.stderr:
                err_output = server_proc.stderr.read().decode("utf-8")
            print(
                f"ERROR: App server failed to start within timeout. {err_output}", file=sys.stderr
            )
            return 1

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:
                print(f"ERROR: Failed to launch Chromium browser: {exc}", file=sys.stderr)
                return 1

            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            )
            blocked_requests: list[str] = []

            def local_only(route):
                if urlparse(route.request.url).hostname in ("127.0.0.1", "localhost"):
                    route.continue_()
                else:
                    blocked_requests.append(urlparse(route.request.url).hostname or "unknown")
                    route.abort()

            context.route("**/*", local_only)
            page = context.new_page()
            local_fonts: list[int] = []
            page.on(
                "response",
                lambda response: (
                    local_fonts.append(response.status)
                    if response.url.endswith("/fonts/NotoSansSC-Regular.otf")
                    else None
                ),
            )

            def enable_accessibility():
                page.wait_for_selector("flt-semantics-placeholder", timeout=15000)
                page.evaluate('document.querySelector("flt-semantics-placeholder")?.click()')
                time.sleep(1)

            def click_semantics_button(name: str) -> bool:
                info = page.evaluate(
                    """(name) => {
                    const btns = Array.from(document.querySelectorAll('flt-semantics[role="button"], flt-semantics[role="tab"]'));
                    let btn = btns.find(n => (n.innerText || "").trim() === name);
                    if (!btn) btn = btns.find(n => (n.getAttribute('aria-label') || "").trim() === name);
                    if (!btn) {
                        const candidates = btns.filter(n => {
                            const txt = (n.innerText || "").trim();
                            const label = (n.getAttribute('aria-label') || "").trim();
                            return txt.includes(name) || label.includes(name);
                        });
                        if (candidates.length > 0) {
                            candidates.sort((a, b) => (a.innerText || "").length - (b.innerText || "").length);
                            btn = candidates[0];
                        }
                    }
                    if (!btn) return null;
                    const r = btn.getBoundingClientRect();
                    return { x: r.left + r.width / 2, y: r.top + r.height / 2, width: r.width, height: r.height, text: btn.innerText };
                }""",
                    name,
                )
                if info and info["width"] > 0 and info["height"] > 0:
                    page.mouse.click(info["x"], info["y"])
                    return True
                return False

            try:
                if reliability:
                    check_reliability(page, base_url, state_dir, enable_accessibility)
                    print(
                        "Reliability browser checks passed: 360/390/430px × 10; synthetic, not a real device."
                    )
                    return 0
                # 1. Open home page
                page.goto(f"{base_url}/", timeout=15000)
                page.wait_for_load_state("domcontentloaded")
                enable_accessibility()

                # Click the top-right Settings icon, then its back arrow (real browser events).
                page.locator("flt-semantics[role='button']").first.click()
                page.wait_for_selector("text=账户与运行信息", timeout=10000)
                page.locator("flt-semantics[role='button']").nth(1).click()
                page.wait_for_selector("text=估值基准日", timeout=10000)

                # U01: no snapshots or notes; no README/CLI import needed.
                entry = page.get_by_role("button", name="前往发现候选", exact=True)
                expect(entry).to_have_count(1)
                expect(page.get_by_role("button", name="开始同业研究", exact=True)).to_have_count(0)
                for width in (360, 390, 430):
                    page.set_viewport_size({"width": width, "height": 844})
                    title = page.get_by_text("暂无关注的公司", exact=True)
                    title.scroll_into_view_if_needed()
                    expect(title).to_be_visible()
                    entry.scroll_into_view_if_needed()
                    expect(entry).to_be_visible()
                    bounds = entry.bounding_box()
                    assert (
                        bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
                    )
                page.set_viewport_size({"width": 390, "height": 844})
                entry.click()
                page.wait_for_selector("text=暂无参照公司", timeout=10000)
                click_settled(page, page.get_by_role("button", name="查找同业", exact=True))
                expect(page).not_to_have_url(re.compile(r"/jobs/"))
                result = page.get_by_role("button", name="查看更新结果", exact=True)
                expect(result).to_be_visible(timeout=20000)
                click_settled(page, result)
                page.wait_for_selector("text=当前完整榜", timeout=20000)
                expect(
                    page.get_by_text("ROE趋势（2023—2025）：连续上升", exact=True).first
                ).to_be_visible()
                expect(page.get_by_text("待核查：复核历史盈利能力下降", exact=True)).to_have_count(
                    0
                )
                expect(
                    page.get_by_text(
                        "系统未核查业务可比性，行业标签不能证明业务相似。", exact=True
                    ).first
                ).to_be_visible()
                for detail in page.get_by_text("支持事实与下一步", exact=True).all():
                    click_settled(page, detail)
                boundary = page.get_by_role("button", name="适用范围与研究边界", exact=True)
                click_settled(page, boundary)
                result_url = page.url
                assert "job=" in result_url

                # Actual values and annual trend, not a shell/canvas or rank-only page.
                for width in (360, 390, 430):
                    page.set_viewport_size({"width": width, "height": 844})
                    pb = page.get_by_text("PB 1.15 倍", exact=False).first
                    pb.scroll_into_view_if_needed()
                    assert pb.is_visible()
                    bounds = pb.bounding_box()
                    assert (
                        bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
                    )
                    assert page.get_by_text("2025年 ROE 14.00%", exact=True).count() == 1
                    comparison = page.get_by_text(
                        "PB低于本次同口径合格样本中位数", exact=False
                    ).first
                    comparison.scroll_into_view_if_needed()
                    assert comparison.is_visible()
                    bounds = comparison.bounding_box()
                    assert (
                        bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
                    )
                    assert (
                        page.get_by_text("ROE趋势（2023—2025）：连续上升", exact=True).count() >= 1
                    )
                    assert page.get_by_text("尚未核查：", exact=False).count() >= 1
                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_role("button", name="展开依据、来源与排除原因", exact=True).click()
                page.get_by_text(
                    "沪深主板、TuShare粗行业", exact=False
                ).scroll_into_view_if_needed()
                assert page.get_by_text(
                    "沪深主板、TuShare粗行业、按市值最多50家", exact=False
                ).is_visible()
                assert page.get_by_text("与参照比较：", exact=False).count() >= 1
                page.get_by_role("button", name="展开依据、来源与排除原因", exact=True).click()

                # Add anchor directly; no automatic ack.
                page.get_by_role("button", name="关注", exact=True).nth(1).click()
                page.wait_for_selector("text=已关注，无需填写笔记", timeout=10000)
                with sqlite3.connect(state_dir / "workspace.sqlite3") as conn:
                    assert conn.execute(
                        "SELECT ack_run_id FROM watch_items WHERE code='600001.SH'"
                    ).fetchone() == (None,)
                click_settled(page, page.get_by_role("button", name="查看关注", exact=True).first)
                page.wait_for_selector("text=本次变化与原判断", timeout=10000)
                time.sleep(1)

                def open_notes():
                    notes_btn = page.get_by_role(
                        "button", name=re.compile("^(展开|收起)可选笔记与状态$")
                    )
                    reason = page.locator("textarea[aria-label*='理由']").first
                    if notes_btn.inner_text().startswith("展开"):
                        click_settled(page, notes_btn)
                    expect(notes_btn).to_have_text("收起可选笔记与状态")
                    reason.wait_for(state="visible", timeout=10000)

                # First review has current facts, not empty old-value arrows or expanded metadata.
                expect(
                    page.get_by_text("当前资料（首次待阅，无已阅基准）", exact=True)
                ).to_be_visible()
                fields = page.get_by_role("button", name="查看全部字段与来源", exact=True)
                group = page.get_by_text("指标与公司状态", exact=True)
                expect(group).not_to_be_visible()
                fields.click()
                expect(group).to_be_visible()
                expect(page.get_by_text("PB（倍） · 首次待阅", exact=True)).to_be_visible()
                metadata = page.get_by_text("2025年报告类型 · 首次待阅", exact=True)
                expect(metadata).not_to_be_visible()
                missing = page.get_by_text("未记录的补充字段（", exact=False)
                missing.scroll_into_view_if_needed()
                time.sleep(0.5)
                missing.click()
                expect(metadata).to_be_visible()
                fields.scroll_into_view_if_needed()
                time.sleep(0.5)
                fields.click()
                expect(group).not_to_be_visible()
                # The optional editor is visible for followed companies, without requiring input.
                expect(page.locator("textarea[aria-label*='理由']").first).to_be_visible()
                open_notes()
                # Select a research state by its accessible name, then explicitly save it.
                click_settled(page, page.get_by_role("button", name="关注状态", exact=True))
                page.get_by_role("button", name="继续研究", exact=True).click()
                # 4. Fill in optional personal notes
                test_reason = "移动端自动化测试理由"
                reason_input = page.locator("textarea[aria-label*='理由']").first
                click_settled(page, reason_input)
                expect(reason_input).to_have_value("")
                reason_input.press_sequentially(test_reason)
                expect(reason_input).to_have_value(test_reason)
                next_step = "核查经营现金流与利润差异"
                next_input = page.get_by_role(
                    "textbox", name="下一步与反证（事件或日期）", exact=False
                )
                click_settled(page, next_input)
                expect(next_input).to_have_value("")
                next_input.press_sequentially(next_step)
                expect(next_input).to_have_value(next_step)

                # Dirty browser Back must show a real modal, not silently lose the edit.
                company_url = page.url
                page.go_back()
                unsaved_prompt = page.get_by_text("有未保存的研究记录", exact=True)
                unsaved_prompt.wait_for(state="visible", timeout=10000)
                page.get_by_role("button", name="继续编辑", exact=True).click()
                unsaved_prompt.wait_for(state="hidden", timeout=10000)
                expect(page).to_have_url(company_url)
                click_settled(page, reason_input)
                expect(reason_input).to_have_value(test_reason)
                assert reason_input.is_visible(), "Cancel did not keep the editor visible"
                click_settled(page, next_input)
                expect(next_input).to_have_value(next_step)

                # 5. Verify Save button accessibility and interactive state via semantic locator
                save_btn = page.get_by_role("button", name="保存笔记与状态", exact=True)
                assert save_btn.is_visible(), "Save button is not visible in accessibility tree"
                assert save_btn.is_enabled(), "Save button is not enabled"
                db_path = state_dir / "workspace.sqlite3"
                blocker = sqlite3.connect(db_path)
                try:
                    blocker.execute("BEGIN IMMEDIATE")
                    click_settled(page, save_btn)
                    expect(page.get_by_role("button", name="保存中…", exact=True)).to_be_disabled(
                        timeout=2000
                    )
                    click_settled(page, reason_input)
                    reason_input.press("End")
                    reason_input.press_sequentially("（保存期间补充反证）")
                finally:
                    blocker.rollback()
                    blocker.close()
                expect(
                    page.get_by_text("已保存提交内容；新增修改尚未保存", exact=True)
                ).to_be_visible()
                with sqlite3.connect(db_path) as conn:
                    assert (
                        conn.execute(
                            "SELECT reason FROM watch_items WHERE code='600001.SH'"
                        ).fetchone()[0]
                        == test_reason
                    )
                test_reason += "（保存期间补充反证）"
                page.go_back()
                unsaved_prompt.wait_for(state="visible")
                page.get_by_role("button", name="继续编辑", exact=True).click()
                unsaved_prompt.wait_for(state="hidden")
                click_settled(page, reason_input)
                expect(reason_input).to_have_value(test_reason)
                click_settled(page, save_btn)

                # UI success is mandatory; persisted data must NEVER bypass this assertion.
                page.get_by_text("保存成功", exact=False).wait_for(state="visible", timeout=10000)

                # 6. Independently verify persistence, in addition to (not instead of) visible feedback
                db_path = state_dir / "workspace.sqlite3"
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM watch_items WHERE code='600001.SH'").fetchone()
                conn.close()

                assert row is not None, "Watch item 600001.SH was not saved in SQLite"
                assert row["reason"] == test_reason, (
                    f"Expected reason '{test_reason}', got '{row['reason']}'"
                )
                assert row["next_check"] == next_step, repr(row["next_check"])
                assert row["status"] == "research"
                expect(page.get_by_text("我的判断：继续研究", exact=True)).to_be_visible()
                assert row["revision"] >= 1, "Revision should be >= 1"
                assert row["ack_run_id"] is None, "Saving notes must not acknowledge facts"
                page.get_by_text("首次待阅：无已阅基准", exact=False).wait_for(state="visible")
                expect(
                    page.get_by_text(
                        "已阅确认界面所示事实并更新对照基准，不代表已核验公告原文或作出投资判断；保存笔记不会自动已阅。",
                        exact=True,
                    )
                ).to_be_visible()
                page.get_by_role("button", name="标记本次变化已阅", exact=True).click()
                page.get_by_text("已标记已阅", exact=False).wait_for(state="visible")
                reviewed = page.get_by_role("button", name="已标记本次已阅", exact=True)
                expect(reviewed).to_be_disabled()
                click_settled(page, save_btn)
                expect(page.get_by_text("保存成功", exact=True)).to_be_visible()
                expect(reviewed).to_be_disabled()
                with sqlite3.connect(db_path) as conn:
                    first_ack = conn.execute(
                        "SELECT ack_run_id FROM watch_items WHERE code='600001.SH'"
                    ).fetchone()[0]
                    assert first_ack is not None

                # U03: real browser back preserves the task's anchor/result; never submits again.
                page.go_back()
                page.wait_for_selector("text=当前完整榜", timeout=10000)
                assert page.url == result_url
                click_settled(page, page.get_by_role("button", name="查看关注", exact=True).first)
                page.wait_for_selector("text=本次变化与原判断", timeout=10000)

                # 7. Navigate back to Home and verify persisted item on home list
                home_nav = page.locator("[aria-label='我的研究']").first
                if home_nav.is_visible():
                    home_nav.click()
                else:
                    assert click_semantics_button("我的研究"), "Could not click 我的研究 tab"
                page.wait_for_function(
                    '() => document.body.innerText.includes("关注清单 (1)")', timeout=10000
                )
                ensure_routine_expanded(page)
                ensure_routine_expanded(page)  # Already expanded must not toggle closed.
                page.get_by_role("button", name="查看详情", exact=True).first.wait_for(
                    state="visible"
                )

                # 8. Reload returns to safe home; never replays a write/update.
                page.reload()
                page.wait_for_load_state("domcontentloaded")
                enable_accessibility()
                page.wait_for_function(
                    '() => document.body.innerText.includes("关注清单 (1)")', timeout=10000
                )
                ensure_routine_expanded(page)
                page.get_by_role("button", name="查看详情", exact=True).first.wait_for(
                    state="visible"
                )

                expect(
                    page.get_by_role("group", name=f"上次下一步：{next_step}", exact=False)
                ).to_be_visible()
                # Pause is personal state only; hidden companies and their notes survive reload.
                with sqlite3.connect(db_path) as conn:
                    preserved = conn.execute(
                        "SELECT reason,next_check,note_url,added_run_id,ack_run_id,ack_at FROM watch_items WHERE code='600001.SH'"
                    ).fetchone()
                page.get_by_role("button", name="暂停关注", exact=True).click()
                page.get_by_text(
                    "已暂停关注，不再计入需要复看；笔记和已阅基准保留。", exact=True
                ).wait_for(state="visible")
                expect(page.get_by_role("button", name="查看详情", exact=True)).to_have_count(0)
                page.reload()
                page.wait_for_load_state("domcontentloaded")
                enable_accessibility()
                expect(page.get_by_role("button", name="查看详情", exact=True)).to_have_count(0)
                page.get_by_role("button", name="显示已暂停 (1)", exact=True).click()
                resume = page.get_by_role("button", name="恢复为等待证据", exact=True)
                for width in (360, 390, 430):
                    page.set_viewport_size({"width": width, "height": 844})
                    resume.scroll_into_view_if_needed()
                    expect(resume).to_be_visible()
                    bounds = resume.bounding_box()
                    assert (
                        bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
                    )
                page.set_viewport_size({"width": 390, "height": 844})
                resume.click()
                page.get_by_text("已恢复为等待证据，笔记和已阅基准保留。", exact=True).wait_for(
                    state="visible"
                )
                with sqlite3.connect(db_path) as conn:
                    assert (
                        conn.execute(
                            "SELECT reason,next_check,note_url,added_run_id,ack_run_id,ack_at FROM watch_items WHERE code='600001.SH'"
                        ).fetchone()
                        == preserved
                    )
                    assert conn.execute(
                        "SELECT status FROM watch_items WHERE code='600001.SH'"
                    ).fetchone() == ("observe",)
                    assert conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0] == 1

                # A real UI submission reaches the independent fixed-watch worker.
                update = page.get_by_role("button", name="更新资料", exact=True)
                for width in (360, 390, 430):
                    page.set_viewport_size({"width": width, "height": 844})
                    update.scroll_into_view_if_needed()
                    bounds = update.bounding_box()
                    assert (
                        bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
                    )
                page.set_viewport_size({"width": 390, "height": 844})
                update.click()
                expect(page).to_have_url(f"{base_url}/")
                page.get_by_text("最近更新：完成 · 关注更新", exact=False).wait_for(
                    state="visible", timeout=15000
                )
                with sqlite3.connect(db_path) as conn:
                    status, phase, result_run = conn.execute(
                        "SELECT status,phase,result_run_id FROM update_jobs WHERE kind='watch'"
                    ).fetchone()
                    assert (status, phase) == ("succeeded", "complete") and result_run, (
                        status,
                        phase,
                        result_run,
                    )
                    assert (
                        conn.execute(
                            "SELECT reason,next_check,note_url,added_run_id,ack_run_id,ack_at FROM watch_items WHERE code='600001.SH'"
                        ).fetchone()
                        == preserved
                    )
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM screen_runs WHERE kind='peer'"
                        ).fetchone()[0]
                        == 1
                    )  # Only the explicit peer scan; watch must not create another board.
                page.reload()
                page.wait_for_load_state("domcontentloaded")
                enable_accessibility()
                expect(page).to_have_url(f"{base_url}/")

                # 9. Wait for the asynchronous home render, then inspect persisted notes.
                detail_btn = page.get_by_role("button", name="查看详情", exact=True).first
                detail_btn.wait_for(state="visible", timeout=10000)
                detail_btn.click()
                page.wait_for_selector("text=本次变化与原判断", timeout=10000)
                click_settled(page, page.get_by_text("PB、历史ROE与入选依据", exact=True))
                expect(
                    page.get_by_text(
                        "固定关注更新不重新排名；行业标签不能证明业务可比。",
                        exact=True,
                    )
                ).to_be_visible()
                expect(page.get_by_text("最近同业名次：", exact=False)).to_be_visible()
                time.sleep(1)
                open_notes()
                detail_reason = page.locator("textarea[aria-label*='理由']").first
                click_settled(page, detail_reason)
                expect(detail_reason).to_have_value(test_reason)

                # Later partial synthetic observation must explain its gaps, not replace the old top.
                partial = json.loads((FIXTURES_DIR / "peer_second_change.json").read_text())
                partial["screened_at"] = datetime.now(UTC).isoformat()
                partial["generated_at"] = partial["screened_at"]
                partial_path = state_dir / "synthetic_partial.json"
                partial_path.write_text(json.dumps(partial))
                import_snapshot(state_dir, partial_path, "demo")
                page.locator("[aria-label='发现候选']").first.click()
                page.wait_for_selector("text=本次尝试：部分完成", timeout=10000)
                page.wait_for_selector("text=当前展示上次完整榜", timeout=10000)
                assert page.get_by_text("PB 1.85 倍", exact=False).count() == 1
                page.get_by_role("button", name="查看本次诊断", exact=True).click()
                page.get_by_text("PB 1.80 倍", exact=False).wait_for(state="visible", timeout=10000)
                assert page.get_by_text("PB 1.80 倍", exact=False).count() == 1
                assert 200 in local_fonts, "Chinese font was not loaded from local assets"
                # Refresh revalidates/reads the selected result; it must not replay its job.
                page.reload()
                page.wait_for_load_state("domcontentloaded")
                enable_accessibility()
                page.wait_for_selector("text=本次尝试：部分完成", timeout=10000)
                page.wait_for_selector("text=当前展示上次完整榜", timeout=10000)
                with sqlite3.connect(state_dir / "workspace.sqlite3") as conn:
                    assert conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0] == 2
                # Real confirmation/cancellation and visible deletion feedback, not DB fallbacks.
                page.locator("[aria-label='我的研究']").first.click()
                expect(page.get_by_text("指标与资料变化", exact=True)).to_be_visible(timeout=10000)
                page.get_by_role("button", name="查看详情", exact=True).first.click()
                page.wait_for_selector("text=本次变化与原判断", timeout=10000)
                # S2: the comparison is visible before confirmation; opening it is read-only.
                for width in (360, 390, 430):
                    page.set_viewport_size({"width": width, "height": 844})
                    delta = page.get_by_text("【变化】PB（倍）：1.85 → 1.8", exact=True)
                    delta.scroll_into_view_if_needed()
                    expect(delta).to_be_visible()
                    bounds = delta.bounding_box()
                    assert (
                        bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
                    )
                page.set_viewport_size({"width": 390, "height": 844})
                expect(
                    page.get_by_text("【变化】2025年ROE（%）：15.8 → 16.5", exact=True)
                ).to_be_visible()
                with sqlite3.connect(db_path) as conn:
                    assert (
                        conn.execute(
                            "SELECT ack_run_id FROM watch_items WHERE code='600001.SH'"
                        ).fetchone()[0]
                        == first_ack
                    )
                open_notes()
                page.get_by_role("button", name="标记本次变化已阅", exact=True).click()
                page.get_by_text("已标记已阅", exact=False).wait_for(state="visible")
                with sqlite3.connect(db_path) as conn:
                    assert (
                        conn.execute(
                            "SELECT ack_run_id FROM watch_items WHERE code='600001.SH'"
                        ).fetchone()[0]
                        != first_ack
                    )
                removal_reason = page.locator("textarea[aria-label*='理由']").first
                click_settled(page, removal_reason)
                expect(removal_reason).to_have_value(test_reason)
                removal_reason.press("End")
                removal_reason.press_sequentially("（未保存）")
                delete_button = page.get_by_role("button", name="删除个人研究记录", exact=True)
                delete_button.scroll_into_view_if_needed()
                delete_button.click()
                page.get_by_role("button", name="取消", exact=True).click()
                click_settled(page, removal_reason)
                expect(removal_reason).to_have_value(test_reason + "（未保存）")
                page.get_by_role("button", name="删除个人研究记录", exact=True).click()
                page.get_by_role("button", name="确认操作", exact=True).click()
                page.get_by_text("已删除个人研究记录，历史扫描保留。", exact=True).wait_for(
                    state="visible"
                )
                page.get_by_text("关注清单 (0)", exact=True).wait_for(state="visible")
                with sqlite3.connect(state_dir / "workspace.sqlite3") as conn:
                    assert conn.execute("SELECT count(*) FROM watch_items").fetchone()[0] == 0
                    assert conn.execute("SELECT count(*) FROM screen_runs").fetchone()[0] == 3
                # Immutable results remain available, and re-adding does not restore old notes/ack.
                page.goto(company_url)
                page.wait_for_load_state("domcontentloaded")
                enable_accessibility()
                page.wait_for_selector("text=本次变化与原判断", timeout=10000)
                # A fresh untracked company keeps the editor closed; following needs no form.
                expect(
                    page.get_by_role("button", name="展开可选笔记与状态", exact=True)
                ).to_be_visible()
                expect(page.locator("textarea[aria-label*='理由']").first).not_to_be_visible()
                page.get_by_role("button", name="关注", exact=True).click()
                expect(page.get_by_role("button", name="已关注", exact=True)).to_be_disabled()
                open_notes()
                fresh_reason = page.locator("textarea[aria-label*='理由']").first
                click_settled(page, fresh_reason)
                expect(fresh_reason).to_have_value("")
                with sqlite3.connect(state_dir / "workspace.sqlite3") as conn:
                    assert conn.execute(
                        "SELECT reason,ack_run_id FROM watch_items WHERE code='600001.SH'"
                    ).fetchone() == ("", None)
                    # Synthetic terminal failure, inserted atomically; worker never receives it.
                    conn.execute("""INSERT INTO update_jobs
                        (job_id,request_id,kind,payload_json,dedupe_key,status,phase,requested_at,updated_at,finished_at)
                        SELECT 'synthetic-failure','synthetic-failure',kind,payload_json,dedupe_key,
                        'failed','failed',requested_at,updated_at,updated_at FROM update_jobs LIMIT 1""")
                    target_date = json.loads(
                        conn.execute(
                            "SELECT payload_json FROM update_jobs WHERE job_id='synthetic-failure'"
                        ).fetchone()[0]
                    )["target_date"]
                page.locator("[aria-label='我的研究']").first.click()
                failed_label = f"600001.SH · {target_date} · 失败"
                click_settled(page, page.get_by_text("更新记录", exact=True))
                click_settled(page, page.get_by_role("button", name=failed_label, exact=True))
                page.get_by_role("button", name="清理失败任务", exact=True).click()
                page.get_by_role("button", name="取消", exact=True).click()
                page.get_by_role("button", name="清理失败任务", exact=True).click()
                page.get_by_role("button", name="确认操作", exact=True).click()
                page.get_by_text("已从列表清理失败任务，防重放记录保留。", exact=True).wait_for(
                    state="visible"
                )
                expect(page.get_by_role("button", name=failed_label, exact=True)).to_have_count(0)
                page.reload()
                page.wait_for_load_state("domcontentloaded")
                enable_accessibility()
                page.get_by_text("关注清单 (1)", exact=True).wait_for(state="visible")
                expect(page.get_by_role("button", name=failed_label, exact=True)).to_have_count(0)
                with sqlite3.connect(state_dir / "workspace.sqlite3") as conn:
                    assert (
                        conn.execute(
                            "SELECT phase FROM update_jobs WHERE job_id='synthetic-failure'"
                        ).fetchone()[0]
                        == "dismissed"
                    )
                # Re-scan is a confirmed submission, not another link to historical results.
                with sqlite3.connect(db_path) as conn:
                    peer_job = conn.execute(
                        "SELECT job_id FROM update_jobs WHERE kind='peer' AND status='succeeded' LIMIT 1"
                    ).fetchone()[0]
                    count_before = conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0]
                page.goto(f"{base_url}/jobs/{peer_job}")
                page.wait_for_load_state("domcontentloaded")
                enable_accessibility()
                page.get_by_role("button", name="重新扫描", exact=True).click()
                page.get_by_role("button", name="取消", exact=True).click()
                with sqlite3.connect(db_path) as conn:
                    assert (
                        conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0]
                        == count_before
                    )
                page.get_by_role("button", name="重新扫描", exact=True).click()
                page.get_by_role("button", name="确认重新扫描", exact=True).click()
                expect(page).not_to_have_url(f"{base_url}/jobs/{peer_job}")
                page.get_by_text("状态：完成", exact=False).wait_for(state="visible", timeout=30000)
                with sqlite3.connect(db_path) as conn:
                    assert (
                        conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0]
                        == count_before + 1
                    )
                page.get_by_role("button", name="查看本次结果/诊断", exact=True).click()
                page.get_by_text("PB 1.85 倍", exact=False).first.wait_for(state="visible")
                # Direct company research works without a previous peer scan or automatic follow.
                page.get_by_role("tab", name="我的研究", exact=False).click()
                code_field = page.get_by_role("textbox", name="指定公司代码", exact=False)
                click_settled(page, code_field)
                expect(code_field).to_be_focused()
                expect(code_field).to_have_value("")
                for index, char in enumerate("600003", start=1):
                    code_field.press_sequentially(char)
                    expect(code_field).to_have_value("600003"[:index])
                # Pasting a complete code must also survive the Flutter input bridge.
                page.keyboard.press("ControlOrMeta+A")
                page.wait_for_function(
                    "el => el.selectionStart === 0 && el.selectionEnd === el.value.length",
                    arg=code_field.element_handle(),
                )
                page.keyboard.insert_text("600003")
                expect(code_field).to_have_value("600003")
                with sqlite3.connect(db_path) as conn:
                    direct_before = conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0]
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM watch_items WHERE code='600003.SH'"
                        ).fetchone()[0]
                        == 0
                    )
                direct = page.get_by_role("button", name="获取公司资料", exact=True)
                old_code_field = code_field.element_handle()
                click_settled(page, direct)
                page.wait_for_function("el => !el.isConnected", arg=old_code_field)
                code_field = page.get_by_role("textbox", name="指定公司代码")
                click_settled(page, code_field)
                page.keyboard.insert_text("600004")
                result = page.get_by_role("button", name="查看更新结果", exact=True)
                expect(result).to_be_visible(timeout=20000)
                expect(page).to_have_url(f"{base_url}/")
                expect(code_field).to_have_value("600004")
                click_settled(page, result)
                expect(page).to_have_url(f"{base_url}/company/600003.SH", timeout=20000)
                expect(page.get_by_role("button", name="关注", exact=True)).to_be_enabled()
                expect(
                    page.get_by_role("button", name="标记本次变化已阅", exact=True)
                ).to_be_disabled()
                with sqlite3.connect(db_path) as conn:
                    assert (
                        conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0]
                        == direct_before + 1
                    )
                    payload = json.loads(
                        conn.execute(
                            "SELECT payload_json FROM update_jobs ORDER BY requested_at DESC LIMIT 1"
                        ).fetchone()[0]
                    )
                    assert (
                        payload["codes"] == ["600003.SH"]
                        and payload["intent"]["code"] == "600003.SH"
                    )
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM watch_items WHERE code='600003.SH'"
                        ).fetchone()[0]
                        == 0
                    )
                # Evidence is open before the optional editor and the review action.
                expect(
                    page.get_by_text("本次接口最新报告期：2026-06-30", exact=False)
                ).to_be_visible()
                expect(page.get_by_text("每股经营现金流：0.85 元/股", exact=True)).to_be_visible()
                for width in (360, 390, 430):
                    page.set_viewport_size({"width": width, "height": 844})
                    action = page.get_by_role("button", name="更新这家公司", exact=True)
                    click_target = action.element_handle()
                    bounds = action.bounding_box()
                    assert bounds
                    page.mouse.move(width / 2, 380)
                    page.mouse.wheel(0, bounds["y"] + bounds["height"] / 2 - 380)
                    page.wait_for_function(
                        "el => el.getBoundingClientRect().height >= 44", arg=click_target
                    )
                    bounds = action.bounding_box()
                    assert (
                        bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width + 1
                    )
                page.set_viewport_size({"width": 390, "height": 844})
                click_settled(page, page.get_by_role("button", name="关注", exact=True))
                expect(page.get_by_role("button", name="已关注", exact=True)).to_be_disabled()
                open_notes()
                reason = page.get_by_role("textbox", name=re.compile("研究理由"))
                click_settled(page, reason)
                expect(reason).to_be_focused()
                expect(reason).to_have_value("")
                page.keyboard.type("支持：盈利增长；反对证据：业务可比性尚未核实")
                expect(reason).to_have_value("支持：盈利增长；反对证据：业务可比性尚未核实")
                note = page.get_by_role("textbox", name="外部笔记链接 (https://)", exact=False)
                click_settled(page, note)
                expect(note).to_be_focused()
                expect(note).to_have_value("")
                page.keyboard.type("https://example.com/research")
                expect(note).to_have_value("https://example.com/research")
                click_settled(page, page.get_by_role("button", name="保存笔记与状态", exact=True))
                expect(page.get_by_text("保存成功", exact=False)).to_be_visible()
                expect(page.get_by_role("button", name="打开研究笔记", exact=True)).to_have_count(1)
                with sqlite3.connect(db_path) as conn:
                    saved = conn.execute(
                        "SELECT reason,note_url,ack_run_id FROM watch_items WHERE code='600003.SH'"
                    ).fetchone()
                    assert saved == (
                        "支持：盈利增长；反对证据：业务可比性尚未核实",
                        "https://example.com/research",
                        None,
                    )
                # The detail-page action freezes this company alone and retains the judgment.
                action = page.get_by_role("button", name="更新这家公司", exact=True)
                click_settled(page, action)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with sqlite3.connect(db_path) as conn:
                        if (
                            conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0]
                            == direct_before + 2
                        ):
                            break
                    page.wait_for_timeout(
                        50
                    )  # Wait for the observed submission, not an arbitrary layout delay.
                expect(action).to_be_enabled()
                with sqlite3.connect(db_path) as conn:
                    assert (
                        conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0]
                        == direct_before + 2
                    )
                    update_id, raw = conn.execute(
                        "SELECT job_id,payload_json FROM update_jobs ORDER BY requested_at DESC LIMIT 1"
                    ).fetchone()
                    assert json.loads(raw)["codes"] == ["600003.SH"]
                click_settled(page, page.get_by_role("button", name="更新详情", exact=True))
                expect(page).to_have_url(f"{base_url}/jobs/{update_id}")
                expect(page.get_by_text("状态：完成", exact=False)).to_be_visible(timeout=20000)
                click_settled(page, page.get_by_role("button", name="查看公司资料", exact=True))
                expect(page).to_have_url(f"{base_url}/company/600003.SH")
                reason = page.get_by_role("textbox", name=re.compile("研究理由"))
                click_settled(page, reason)
                expect(reason).to_have_value("支持：盈利增长；反对证据：业务可比性尚未核实")
                with sqlite3.connect(db_path) as conn:
                    assert (
                        conn.execute(
                            "SELECT ack_run_id FROM watch_items WHERE code='600003.SH'"
                        ).fetchone()[0]
                        is None
                    )
                # Date-only updates can be reviewed on the home card at every mobile width.
                from a_stock_tracker.auth import create_demo_actor
                from a_stock_tracker.services import get_company_context, mark_seen, save_watch

                actor = create_demo_actor()
                snapshot = json.loads((FIXTURES_DIR / "peer_complete_v1.json").read_text())
                report = get_company_context(actor, "600003.SH", state_dir, "demo")[
                    "research_report"
                ]
                next(r for r in snapshot["rows"] if r["code"] == "600004.SH")["research_report"] = (
                    report
                )
                date_path = state_dir / "date-only.json"

                def import_date(day):
                    snapshot["data_date"] = day
                    time.sleep(0.01)
                    now_ts = datetime.now(UTC).isoformat()
                    snapshot["screened_at"] = snapshot["generated_at"] = now_ts
                    for fact in snapshot["rows"]:
                        fact["valuation_date"] = day
                    date_path.write_text(json.dumps(snapshot))
                    return import_snapshot(state_dir, date_path, "demo")

                baseline = import_date("2026-09-25")
                item = save_watch(actor, "600004.SH", baseline, {}, 0, state_dir, "demo")
                mark_seen(actor, "600004.SH", baseline, item["revision"], state_dir, "demo")
                with sqlite3.connect(db_path) as conn:
                    jobs_before_ack = conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0]
                for width, day in ((360, "2026-09-26"), (390, "2026-09-27"), (430, "2026-09-28")):
                    current = import_date(day)
                    page.set_viewport_size({"width": width, "height": 844})
                    page.goto(f"{base_url}/")
                    page.wait_for_load_state("domcontentloaded")
                    enable_accessibility()
                    page.get_by_text("关注清单", exact=False).wait_for(state="visible")
                    ensure_routine_expanded(page)
                    ack = page.get_by_role("button", name="标记本次已阅", exact=True)
                    expect(
                        page.get_by_role(
                            "group",
                            name=re.compile(
                                r"^示例公司丁 .*已阅确认界面所示事实并更新对照基准，不代表已核验公告原文或作出投资判断。"
                            ),
                        )
                    ).to_be_visible()
                    click_settled(page, ack)
                    expect(page).to_have_url(f"{base_url}/")
                    expect(
                        page.get_by_text("示例公司丁：已标记本次已阅。", exact=True)
                    ).to_be_visible()
                    expect(ack).to_have_count(0)
                    with sqlite3.connect(db_path) as conn:
                        assert (
                            conn.execute(
                                "SELECT ack_run_id FROM watch_items WHERE code='600004.SH'"
                            ).fetchone()[0]
                            == current
                        )
                        assert (
                            conn.execute("SELECT count(*) FROM update_jobs").fetchone()[0]
                            == jobs_before_ack
                        )

                # A positive mean must not hide a negative latest ROE on the default board.
                from a_stock_tracker.research import rank_peers
                from a_stock_tracker.services import get_peer_discover

                snapshot = json.loads((FIXTURES_DIR / "peer_complete_v1.json").read_text())
                snapshot["data_date"] = "2026-09-29"
                for fact in snapshot["rows"]:
                    fact["valuation_date"] = snapshot["data_date"]
                for fact, values in zip(
                    snapshot["rows"], ([30, 20, -5], [10, 20, 15], [10, -5, 15])
                ):
                    for annual, value in zip(fact["annual_roes"], values):
                        annual["roe"] = value
                    fact["roe_mean"] = sum(values) / 3
                snapshot["results"] = rank_peers(snapshot["rows"], snapshot["anchor"], ["600001"])
                now_ts = datetime.now(UTC).isoformat()
                snapshot["screened_at"] = snapshot["generated_at"] = now_ts
                caution_path = state_dir / "cautions.json"
                caution_path.write_text(json.dumps(snapshot))
                caution_run = import_snapshot(state_dir, caution_path, "demo")
                board = get_peer_discover(actor, "600001.SH", state_dir, "demo")
                assert board["run_id"] == caution_run
                assert board["results"]["ranking"] == snapshot["results"]["ranking"]
                assert board["results"]["top"] == snapshot["results"]["top"]
                # A fresh tab must not inherit the previous page's pinned discovery board.
                page.close()
                page = context.new_page()
                page.goto(f"{base_url}/discover?anchor=600001.SH")
                enable_accessibility()
                page.wait_for_selector("text=当前完整榜", timeout=10000)
                for width in (360, 390, 430):
                    page.set_viewport_size({"width": width, "height": 844})
                    for text in (
                        "ROE趋势（2023—2025）：连续下降；最新年度ROE为负，均值不能掩盖这一点",
                        "ROE趋势（2023—2025）：非单调变化",
                        "ROE趋势（2023—2025）：非单调变化；历史年度ROE出现负值，均值不能掩盖这一点",
                        "待核查：复核历史盈利能力下降",
                        "系统未核查业务可比性，行业标签不能证明业务相似。",
                    ):
                        caution = page.get_by_text(text, exact=True).first
                        caution.scroll_into_view_if_needed()
                        expect(caution).to_be_visible()
                        bounds = caution.bounding_box()
                        assert (
                            bounds
                            and bounds["x"] >= 0
                            and bounds["x"] + bounds["width"] <= width + 1
                        )
                # Flet may attempt optional CDN resources; every external request was aborted.
                print(f"External requests blocked (none allowed): {sorted(set(blocked_requests))}")
                print(
                    "Mobile browser test passed (360/390/430px, synthetic, not a real device): empty -> submit -> worker -> comparison -> add -> dirty Back/cancel -> visible save feedback -> back -> reload -> pause/reload/resume -> fixed watch submit/worker/reload -> partial/old board -> acknowledged/current comparison -> explicit ack -> delete/cancel/re-add -> dismiss failure/reload -> rescan -> direct company/input/paste -> latest financial evidence -> follow/save/note link -> single-company update/result/judgment preserved."
                )
                return 0
            except Exception as exc:
                try:
                    page.screenshot(path=str(SCREENSHOT_PATH))
                    print(f"Saved failure screenshot to {SCREENSHOT_PATH}", file=sys.stderr)
                except Exception:
                    pass
                print(f"Mobile browser test assertion failed: {exc}", file=sys.stderr)
                traceback.print_exc()
                return 1
            finally:
                context.close()
                browser.close()
    finally:
        server_proc.terminate()
        server_proc.wait(timeout=5)
        worker_proc.terminate()
        worker_proc.wait(timeout=5)
        temp_dir.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reliability",
        action="store_true",
        help="Repeat the focused input/reconnect flow 10 times per width",
    )
    sys.exit(main(reliability=parser.parse_args().reliability))
