"""Real-browser regression for the server-rendered Web on a synthetic demo server.

Run with `make test-mobile` (python -m tests.test_mobile). Synthetic data only; external
requests are blocked. This is not a real-device or production acceptance test.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import closing
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (360, 390, 430, 1280)
COLD_START_BUDGET_MS = 2000
# 10 Mbps down / 1 Mbps up / 80 ms latency, in DevTools units (bytes per second).
MOBILE_NETWORK = {
    "offline": False,
    "latency": 80,
    "downloadThroughput": 10_000_000 / 8,
    "uploadThroughput": 1_000_000 / 8,
}


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(url: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            # Never route the loopback probe through a host-wide HTTP proxy.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url + "/login", timeout=2):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("synthetic demo server did not start")


def assert_layout(page: Page, width: int, where: str) -> None:
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0, f"{where}@{width}: horizontal overflow {overflow}px"
    small = page.evaluate(
        """() => [...document.querySelectorAll('main .btn, main .link, summary, .check, .shell .nav, .jump-bar a, .mini-head a, .tabs a')]
            .filter(el => el.offsetParent !== null)
            .map(el => [el.textContent.trim().slice(0, 20), el.getBoundingClientRect().height])
            .filter(([, h]) => h < 47.5)"""
    )
    assert not small, f"{where}@{width}: touch targets under 48px {small}"
    body = page.evaluate("parseFloat(getComputedStyle(document.body).fontSize)")
    assert body >= 16, f"{where}@{width}: body text {body}px"


def confirm_and_submit(page: Page) -> None:
    # Keyboard consent: focus the checkbox and toggle it with Space.
    page.focus("input[name=confirmed]")
    page.keyboard.press("Space")
    expect(page.locator("input[name=confirmed]")).to_be_checked()
    page.click("button[type=submit]")
    page.wait_for_load_state()


def below_mini_head(page: Page, selector: str) -> bool:
    return page.evaluate(
        """(selector) => document.querySelector(selector).getBoundingClientRect().top
            >= document.querySelector('[data-mini-head]').getBoundingClientRect().bottom""",
        selector,
    )


def report_jumps(page: Page, width: int) -> None:
    # Instant scrolling keeps positions deterministic; smooth scrolling is covered above.
    page.emulate_media(reduced_motion="reduce")
    mini = page.locator("[data-mini-head]")
    page.evaluate("window.scrollTo(0, 0)")
    expect(mini).to_be_hidden()
    jumps = page.locator(".jump-bar a")
    expect(jumps).to_have_text(
        ["最强反证", "主要缺口", "业务", "财务", "估值方法", "改变判断的条件", "原文事实"]
    )
    assert_layout(page, width, "jump-bar")
    # The fact list opens from the bar; past the head, the mini-head names code and date.
    jumps.last.click()
    expect(page.locator("details#facts-600519\\.SH")).to_have_attribute("open", "")
    expect(mini).to_be_visible()
    expect(mini).to_contain_text("600519.SH")
    expect(mini).to_contain_text("资料截至")
    assert_layout(page, width, "mini-head")
    jumps.nth(5).evaluate("link => link.scrollIntoView({inline: 'nearest'})")
    jumps.nth(5).click()
    heading = page.locator("#section-600519\\.SH-4")
    expect(heading).to_be_in_viewport()
    assert below_mini_head(page, "#section-600519\\.SH-4"), f"heading under mini-head@{width}"
    # The mini-head leads back to the report head, where it hides again.
    mini.locator("a").click()
    expect(page.locator("#report-600519\\.SH")).to_be_in_viewport()
    expect(mini).to_be_hidden()
    page.emulate_media(reduced_motion="no-preference")


def main_flow(page: Page, base: str, width: int) -> None:
    page.goto(base + "/login")
    page.click("text=进入合成演示")
    expect(page.locator("h1")).to_have_text("研究首页")
    assert_layout(page, width, "home")
    entries = page.locator("nav.entries a")
    expect(entries).to_have_text(["从初筛找公司", "独立价格参考（不调用模型）"])
    expect(page.locator("nav.tabs a")).to_have_text(["概览", "财报研究", "初筛", "价格参考"])

    # Financial: the home code field only pre-fills the confirmation page.
    page.fill("form.quick input[name=code]", "600519")
    page.click("form.quick button")
    expect(page.locator("input[name=codes]")).to_have_value("600519")
    expect(page.locator("input[name=confirmed]")).not_to_be_checked()
    assert_layout(page, width, "form")
    # Unconfirmed is refused and nothing runs; confirmed starts once.
    page.click("button[type=submit]")
    expect(page.locator(".feedback")).to_contain_text("请先勾选确认")
    # Editing the scope after confirming withdraws the confirmation.
    page.check("input[name=confirmed]")
    page.fill("input[name=codes]", "600000")
    expect(page.locator("input[name=confirmed]")).not_to_be_checked()
    page.fill("input[name=codes]", "600519")
    confirm_and_submit(page)
    expect(page.locator("h1")).to_have_text("财报研究")
    expect(page.locator("main")).to_contain_text("最强反证")
    summary = page.locator("summary", has_text="原文事实")
    summary.focus()
    page.keyboard.press("Enter")
    expect(page.locator(".fact").first).to_contain_text("PDF 第 12 页")
    assert_layout(page, width, "report")
    # A citation reopens the collapsed fact list and lands on the cited fact.
    page.keyboard.press("Enter")
    expect(page.locator(".fact").first).to_be_hidden()
    page.locator(".notice.caution a.citation").first.click()
    cited = page.locator(".fact:target")
    expect(cited).to_be_visible()
    expect(cited).to_be_in_viewport()
    report_jumps(page, width)

    # The report code opens the company dossier: latest report and a price entry.
    page.click("text=600519.SH · 公司档案")
    expect(page.locator("h1")).to_have_text("演示公司")
    expect(page.locator("a.btn", has_text="阅读最新研报")).to_be_visible()
    expect(page.locator("main")).to_contain_text("尚未核对价格参考。")
    assert_layout(page, width, "company")

    # Price reference is a separate confirmation, prefilled but never auto-submitted.
    page.click("text=核对价格参考（不调用模型）")
    expect(page.locator("textarea[name=codes]")).to_have_value("600519.SH")
    expect(page.locator("input[name=confirmed]")).not_to_be_checked()
    page.fill("textarea[name=codes]", "600001")
    confirm_and_submit(page)
    expect(page.locator("main")).to_contain_text("1 项价格参考 · 1 家显式股票池")
    page.click("text=查看价格参考")
    expect(page.locator("main")).to_contain_text("观察线与失效线")
    expect(page.locator(".window")).to_contain_text("后续 5 个交易日")
    expect(page.locator(".perforation")).to_be_visible()
    assert_layout(page, width, "price")

    # Screen: candidates first, then the paged ranking with a filter.
    page.goto(base + "/analysis?mode=screen")
    confirm_and_submit(page)
    expect(page.locator("h1")).to_have_text("选择公司研究财报")
    assert_layout(page, width, "screen")
    page.fill("input[name=query]", "演示")
    page.click("text=筛选完整排名")
    expect(page.locator("article.candidate")).to_have_count(1)
    assert_layout(page, width, "ranking")

    # Gap and empty price states stay distinct.
    for code, text in (("600003", "资料核验失败"), ("600004", "0 项价格参考")):
        page.goto(base + "/analysis?mode=swing&code=" + code)
        confirm_and_submit(page)
        expect(page.locator("main")).to_contain_text(text)

    # Home tabs are plain links; the price tab lists only price references.
    page.goto(base + "/")
    page.locator("nav.tabs a", has_text="价格参考").click()
    expect(page.locator("nav.tabs a[aria-current=page]")).to_have_text("价格参考")
    expect(page.locator("main")).to_contain_text("资料缺口")
    expect(page.locator("main")).not_to_contain_text("全市场初筛")
    assert_layout(page, width, "home-tab")

    # Read failure offers a retry without inventing a result.
    page.goto(base + "/analysis/" + "f" * 32)
    expect(page.locator("h1")).to_have_text("研究状态暂不可确认")
    expect(page.locator("a.btn", has_text="重新查看")).to_be_visible()

    # Logout isolates content: back navigation and direct URLs land on login.
    page.goto(base + "/settings")
    assert_layout(page, width, "settings")
    page.click("text=退出登录")
    expect(page.locator("h1")).to_have_text("登录")
    page.go_back()
    page.reload()
    expect(page.locator("h1")).to_have_text("登录")
    for retired in ("/published", "/opportunity/600001.SH", "/api/session"):
        response = page.goto(base + retired)
        assert response is not None and response.status == 404, retired


def expiry_and_polling(browser, base: str) -> None:
    # Expiry: an already open page hides levels once the Shanghai date passes the expiry,
    # through the page's own timer, without navigating or reloading.
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(base + "/login")
    page.click("text=进入合成演示")
    page.goto(base + "/analysis?mode=swing&code=600001")
    confirm_and_submit(page)
    run = page.url
    session = context.storage_state()
    for url in (base + "/", run, run + "/600001.SH"):
        # A fresh fake clock per page, starting from real time.
        viewer = browser.new_context(viewport={"width": 390, "height": 844}, storage_state=session)
        opened = viewer.new_page()
        opened.clock.install()
        opened.goto(url)
        levels = opened.locator("[data-levels]").first
        expect(levels).to_be_visible()
        # Jump the wall clock past expiry, then let the page's 60 s interval run once.
        opened.clock.set_system_time(time.time() + 31 * 86400)
        opened.clock.fast_forward(61_000)
        assert opened.url == url, "expiry check must not navigate"
        expect(levels).to_be_hidden()
        expect(opened.locator("[data-expired]").first).to_be_visible()
        expect(opened.locator("main")).not_to_contain_text("失效线 11.00", use_inner_text=True)
        viewer.close()

    # Polling: a revoked session stops and clears progress; a status change reloads.
    run = "/analysis/" + "e" * 32
    running = (
        '<!doctype html><html><head><script src="/static/app.js" defer></script></head><body>'
        f'<section data-poll="{run}/state.json" data-status="running"><h1>后台研究中</h1>'
        "<p>私有进度</p></section></body></html>"
    )
    state = {"status": 401, "served": 0}

    def serve_run(route):
        state["served"] += 1
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=running)

    def serve_state(route):
        if state["status"] == 401:
            route.fulfill(status=401, json={"error": "unauthorized"})
        else:
            route.fulfill(status=200, json={"status": "completed"})

    page.route(base + run, serve_run)
    page.route(base + run + "/state.json", serve_state)
    page.goto(base + run)
    expect(page.locator("section")).to_contain_text("登录已失效", timeout=8000)
    expect(page.locator("section")).not_to_contain_text("私有进度")
    state.update(status=200, served=0)
    page.goto(base + run)
    deadline = time.monotonic() + 8
    while state["served"] < 2 and time.monotonic() < deadline:
        page.wait_for_timeout(200)
    assert state["served"] >= 2, "status change did not reload the page"
    context.close()


def cold_start(browser, base: str) -> dict:
    context = browser.new_context(viewport={"width": 390, "height": 844})
    login = context.request.post(base + "/login/demo", headers={"Origin": base})
    assert login.ok
    page = context.new_page()
    cdp = context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    cdp.send("Network.emulateNetworkConditions", MOBILE_NETWORK)
    page.goto(base + "/")
    page.locator("form.quick button").wait_for()
    metrics = page.evaluate(
        """() => {
            const nav = performance.getEntriesByType('navigation')[0];
            const resources = performance.getEntriesByType('resource');
            return {
              ready_ms: Math.round(performance.now()),
              transfer_bytes: nav.transferSize + resources.reduce((s, r) => s + r.transferSize, 0),
              resources: resources.length,
            };
        }"""
    )
    context.close()
    assert metrics["ready_ms"] <= COLD_START_BUDGET_MS, metrics
    return metrics


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "APP_MODE": "demo",
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "PUBLIC_BASE_URL": base,
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "a_stock_tracker.web"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    external: list[str] = []
    try:
        wait_for_server(base)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            def block(route):
                external.append(route.request.url)
                route.abort()

            metrics = cold_start(browser, base)
            print(f"Synthetic cold entry (10Mbps/80ms, cache disabled): {metrics}")
            for width in WIDTHS:
                context = browser.new_context(viewport={"width": width, "height": 900})
                context.route(lambda url: not url.startswith(base), block)
                main_flow(context.new_page(), base, width)
                context.close()
            expiry_and_polling(browser, base)
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    assert not external, f"external requests attempted: {external}"
    print(
        "Browser passed: 360/390/430px + desktop; home code field, consent refusal, financial report, "
        "citations by keyboard, report jump bar and sticky mini-head, company dossier, home tabs, "
        "price time window, "
        "price reference prefill and detail, screen ranking filter, "
        "gap/empty/read-failure states, logout isolation, retired URLs 404, local expiry "
        "hiding, polling stop on 401 and reload on change. Synthetic, not a real device."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
