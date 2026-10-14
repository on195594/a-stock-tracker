"""Real browser clicks for the read-only app; loopback, synthetic, no private data."""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright


def wait_for_server(url: str, timeout_sec: int = 20) -> bool:
    deadline = time.monotonic() + timeout_sec
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def click_settled(page, control):
    """Scroll the painted Flutter canvas, not just its invisible semantics."""
    deadline = time.monotonic() + 5

    def remaining_ms():
        remaining = (deadline - time.monotonic()) * 1000
        if remaining <= 0:
            raise PlaywrightTimeoutError("Control did not settle inside viewport within 5000ms")
        return remaining

    before = None
    box = None
    scrolls = 0
    try:
        control.wait_for(state="visible", timeout=remaining_ms())
        while time.monotonic() < deadline:
            element = control.element_handle(timeout=remaining_ms())
            page.wait_for_function(
                """el => {
                    if (!el.isConnected) return true;
                    const r = el.getBoundingClientRect();
                    const geometry = [r.x, r.y, r.width, r.height].join(',');
                    if (el._testGeometry !== geometry) {
                        el._testGeometry = geometry;
                        el._testStableSince = performance.now();
                    }
                    return performance.now() - el._testStableSince >= 250;
                }""",
                arg=element,
                timeout=remaining_ms(),
            )
            if not element.evaluate("el => el.isConnected"):
                continue  # Flutter may replace semantics while laying out the page.
            box = element.bounding_box()
            if before is None:
                before = box
            assert box and box["width"] > 0 and box["height"] > 0
            height = page.viewport_size["height"]
            if box["y"] >= 0 and box["y"] + box["height"] <= height:
                element.click(timeout=remaining_ms())
                if element.evaluate("el => el.matches('input, textarea')"):
                    # Flutter's editing connection settles after the canvas click.
                    element.evaluate("el => { el._testFocusedSince = null; }")
                    page.wait_for_function(
                        """el => {
                            if (!el.isConnected || el !== document.activeElement) {
                                el._testFocusedSince = null;
                                return false;
                            }
                            el._testFocusedSince ??= performance.now();
                            return performance.now() - el._testFocusedSince >= 250;
                        }""",
                        arg=element,
                        timeout=remaining_ms(),
                    )
                return
            # Flutter can consume less than the requested wheel delta. Re-measure
            # after it settles instead of waiting forever on an offscreen target.
            center = height / 2
            page.mouse.move(page.viewport_size["width"] / 2, center)
            page.mouse.wheel(0, box["y"] + box["height"] / 2 - center)
            scrolls += 1
            element.evaluate("el => { el._testStableSince = performance.now(); }")
        raise PlaywrightTimeoutError("Control did not settle inside viewport within 5000ms")
    except PlaywrightTimeoutError:
        print(
            "Unsettled synthetic control:",
            {
                "url": page.url,
                "viewport": page.viewport_size,
                "before": before,
                "current": box,
                "scrolls": scrolls,
            },
            flush=True,
        )
        raise


def serve_ui_cases(case_file: str, uncompressed_assets: bool = False) -> None:
    """Test-only data substitution; use the real app, routes and actor checks."""
    import uvicorn

    from a_stock_tracker import app, opportunity_demo
    from a_stock_tracker.research import ScreenError

    assert app.APP_MODE == "demo" and app.HOST == "127.0.0.1"
    from a_stock_tracker.research_client import DemoResearchClient, ResearchQuoteChanged

    original_submit = DemoResearchClient.submit
    original_recent = DemoResearchClient.recent

    def recent(client):
        # Isolate published-batch state cases from the valid research created earlier.
        if json.loads(Path(case_file).read_text()) not in {"normal", "quote_changed"}:
            return []
        return original_recent(client)

    DemoResearchClient.recent = recent

    def submit(client, payload):
        if json.loads(Path(case_file).read_text()) == "quote_changed":
            Path(case_file).write_text(json.dumps("normal"))
            raise ResearchQuoteChanged("预算或模型已变化，请重新确认")
        return original_submit(client, payload)

    DemoResearchClient.submit = submit
    original = opportunity_demo.demo_board
    failed = False

    def board(scenario="reference"):
        nonlocal failed
        case = json.loads(Path(case_file).read_text())
        if case == "missing":
            return None
        if case == "error" and not failed:
            failed = True
            raise ScreenError("synthetic read failure")
        result = original(scenario)
        if not result["rows"]:
            return result
        row = result["rows"][0]
        if case == "long":
            row["name"] = "合成的超长公司名称用于验证完整身份换行" * 3
            result["source"] = "仅本地合成资料，未请求行情或模型。" * 8
        elif case == "expired":
            row["expired"] = True
        elif case == "gap":
            result = original("gap")
        elif case == "excluded":
            from tests.test_opportunities import legacy_board

            result = legacy_board()
            result["rows"][0].update(status="excluded", reasons=["旧规则距离超过8%"])
            result["rows"][0]["facts"]["distance"] = "旧规则距离 9.26%"
        elif case == "wide":
            result = original("wide")
        elif case == "zero":
            result["rows"] = []
        return result

    opportunity_demo.demo_board = board
    if uncompressed_assets:
        app.PublicAssetCompression = lambda inner: inner
        app.asgi_app = app.get_asgi_app()
    uvicorn.run(app.asgi_app, host=app.HOST, port=app.PORT, access_log=False)


def assert_layout(page, width):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for control in page.get_by_role("heading").all() + page.get_by_role("button").all():
        box = control.bounding_box()
        if box and box["width"]:
            assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1, box
    for button in page.get_by_role("button").all():
        box = button.bounding_box()
        # Flutter clips semantics at the viewport edge; measure complete touch targets.
        if (
            box
            and box["height"]
            and 0 < box["y"]
            and box["y"] + box["height"] < page.viewport_size["height"]
        ):
            assert box["height"] >= 47, box


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="stock_opportunities_mobile_") as tmp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = {
            **{
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("TUSHARE", "GITHUB_", "OPPORTUNITY_"))
            },
            "APP_MODE": "demo",
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "PUBLIC_BASE_URL": base,
            "FLET_WEB_NO_CDN": "true",
        }
        case_file = Path(tmp) / "case.json"
        case_file.write_text(json.dumps("normal"))
        with (Path(tmp) / "server.log").open("w") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "from tests.test_mobile import serve_ui_cases; import sys; serve_ui_cases(sys.argv[1], sys.argv[2] == 'identity')",
                    str(case_file),
                    "identity" if "--uncompressed-assets" in sys.argv else "gzip",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                assert wait_for_server(base), "demo server did not start"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for path in (
                    "/research",
                    "/discover",
                    "/company/600001.SH",
                    "/jobs/old",
                    "/upload",
                ):
                    try:
                        opener.open(base + path, timeout=3)
                    except urllib.error.HTTPError as exc:
                        assert exc.code == 404
                    else:
                        raise AssertionError(f"Retired endpoint still accessible: {path}")
                with sync_playwright() as p:
                    # Block external hosts at DNS level: Playwright routing would disable the
                    # HTTP cache that lets the client reuse the preloaded font.
                    browser = p.chromium.launch(
                        headless=True,
                        args=[
                            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1"
                        ],
                    )
                    context = browser.new_context(viewport={"width": 390, "height": 844})
                    blocked = set()
                    fonts = []
                    page = context.new_page()
                    context.on(
                        "requestfailed",
                        lambda request: (
                            blocked.add(host)
                            if (host := urlparse(request.url).hostname)
                            not in ("127.0.0.1", "localhost")
                            else None
                        ),
                    )
                    if "--mobile-network" in sys.argv:
                        cdp = context.new_cdp_session(page)
                        cdp.send("Network.enable")
                        cdp.send(
                            "Network.emulateNetworkConditions",
                            {
                                "offline": False,
                                "latency": 80,
                                "downloadThroughput": 10_000_000 / 8,
                                "uploadThroughput": 1_000_000 / 8,
                            },
                        )
                    context.on(
                        "response",
                        lambda response: (
                            fonts.append(response.status)
                            if response.url.endswith("/fonts/NotoSansSC-Subset.woff2")
                            else None
                        ),
                    )
                    for width in (360, 390, 430):
                        page.set_viewport_size({"width": width, "height": 844})
                        timeout = 60000 if "--mobile-network" in sys.argv else 15000
                        page.goto(base, timeout=timeout)
                        page.wait_for_selector("flt-semantics-placeholder", timeout=timeout)
                        page.evaluate('document.querySelector("flt-semantics-placeholder").click()')
                        # Flet renders content only after page fonts load; allow the cold font.
                        expect(
                            page.get_by_role("heading", name="研究首页", exact=True)
                        ).to_be_visible(timeout=timeout)
                        expect(
                            page.get_by_role("heading", name="最近研究", exact=True)
                        ).to_be_visible()
                        assert_layout(page, width)
                        if width == 360:
                            page.wait_for_function(
                                """() => performance.getEntriesByType('resource')
                                .some(r => r.name.endsWith('/fonts/NotoSansSC-Subset.woff2'))""",
                                timeout=timeout,
                            )
                            metrics = page.evaluate("""() => {
                                const resources = performance.getEntriesByType('resource')
                                    .filter(r => r.name.startsWith(location.origin));
                                return {
                                    ready_ms: Math.round(performance.now()),
                                    encoded_bytes: resources.reduce((n, r) => n + r.encodedBodySize, 0),
                                    // Cache hits report a body size but cost no network bytes.
                                    transfer_bytes: resources.reduce((n, r) => n + r.transferSize, 0),
                                    decoded_bytes: resources.reduce((n, r) => n + r.decodedBodySize, 0),
                                };
                            }""")
                            network = (
                                "10Mbps/80ms" if "--mobile-network" in sys.argv else "unthrottled"
                            )
                            encoding = "identity" if "--uncompressed-assets" in sys.argv else "gzip"
                            print(f"Synthetic cold entry ({network}, {encoding}): {metrics}")
                            if "--performance-only" in sys.argv:
                                browser.close()
                                return 0
                        assert page.get_by_role("heading", level=1).count() == 1
                        assert page.get_by_role("tablist").count() == 0
                        assert (
                            page.get_by_role(
                                "button", name=re.compile("历史研究|兼容入口|我的研究|发现候选")
                            ).count()
                            == 0
                        )
                        click_settled(
                            page,
                            page.get_by_role("button", name="查看价格参考 · 演示公司", exact=True),
                        )
                        expect(page.get_by_text("观察线与失效线", exact=True)).to_be_visible()
                        assert_layout(page, width)
                        expect(page.get_by_text(re.compile("MA20.*MA60"))).to_be_visible()
                        page.mouse.move(width / 2, 200)
                        page.mouse.wheel(0, -5000)
                        click_settled(page, page.get_by_role("button", name="账户", exact=True))
                        click_settled(page, page.get_by_role("button", name="账户", exact=True))
                        expect(page.get_by_text("当前模式: demo")).to_be_visible()
                        click_settled(
                            page, page.get_by_role("button", name="返回上一页", exact=True)
                        )
                        expect(page).to_have_url(base + "/opportunity/600001.SH")
                        click_settled(
                            page, page.get_by_role("button", name="返回价格参考", exact=True)
                        )
                        expect(page).to_have_url(base + "/")
                        page.mouse.move(width / 2, 200)
                        page.mouse.wheel(0, -5000)
                        click_settled(page, page.get_by_role("button", name="账户", exact=True))
                        click_settled(page, page.get_by_role("button", name="退出登录", exact=True))
                        expect(page.get_by_text("仅本地合成演示")).to_be_visible()
                        click_settled(page, page.get_by_role("button", name="进入演示", exact=True))
                        expect(
                            page.get_by_role("heading", name="价格纪律参考", exact=True)
                        ).to_be_visible()
                        click_settled(
                            page, page.get_by_role("button", name="开始选股与研究", exact=True)
                        )
                        expect(
                            page.get_by_role("heading", name="选股与研究", exact=True)
                        ).to_be_visible()
                        start = page.get_by_role("button", name="确认启动研究", exact=True)
                        expect(page.get_by_text("初筛门槛：0＜PB≤3", exact=False)).to_be_visible()
                        expect(
                            page.get_by_text("总市值≥50亿元，不设上限", exact=False)
                        ).to_be_visible()
                        expect(start).to_be_disabled()
                        click_settled(page, page.get_by_role("checkbox"))
                        expect(start).to_be_enabled()
                        click_settled(page, start)
                        expect(
                            page.get_by_role("heading", name="财报筛选与研究", exact=True)
                        ).to_be_visible(timeout=10000)
                        expect(page.get_by_text("这是筛后同业排名", exact=False)).to_be_visible()
                        click_settled(page, page.get_by_role("button", name="依据", exact=True))
                        expect(page.get_by_text("冻结门槛：0＜PB≤3", exact=False)).to_be_visible()
                        assert_layout(page, width)
                        click_settled(
                            page,
                            page.get_by_role(
                                "button", name="继续未覆盖初筛（核对预算）", exact=True
                            ),
                        )
                        continuation = page.get_by_role(
                            "button", name="确认新增预算并继续初筛", exact=True
                        )
                        expect(continuation).to_be_disabled()
                        expect(
                            page.get_by_text("本次另增最多 21 次数据请求", exact=False)
                        ).to_be_visible()
                        assert_layout(page, width)
                        click_settled(page, page.get_by_role("checkbox"))
                        click_settled(page, continuation)
                        expect(
                            page.get_by_role("heading", name="财报筛选与研究", exact=True)
                        ).to_be_visible(timeout=10000)
                        screen_url = page.url
                        search = page.get_by_role("textbox", name="按代码、名称或行业筛选")
                        click_settled(page, search)
                        search.press("ControlOrMeta+A")
                        # Target the editable field rather than whichever canvas control has focus.
                        search.press_sequentially("600004")
                        expect(search).to_have_value("600004")
                        expect(
                            page.get_by_role("heading", name="无资料示例 · 600004.SH", exact=True)
                        ).to_be_visible()
                        expect(
                            page.get_by_role("heading", name="演示公司 · 600001.SH", exact=True)
                        ).to_have_count(0)
                        click_settled(page, search)
                        search.press("ControlOrMeta+A")
                        search.press_sequentially("合成银行")
                        expect(search).to_have_value("合成银行")
                        expect(
                            page.get_by_role("heading", name="演示公司 · 600001.SH", exact=True)
                        ).to_be_visible()
                        click_settled(
                            page,
                            page.get_by_role("button", name="查看价格纪律参考", exact=True).first,
                        )
                        page.get_by_role("textbox", name="股票代码").focus()
                        expect(page.get_by_role("textbox", name="股票代码")).to_have_value(
                            "600001.SH"
                        )
                        expect(page.get_by_text("不调用模型", exact=False)).to_be_visible()
                        expect(
                            page.get_by_text("输出 token 与费用无硬上限", exact=False)
                        ).to_have_count(0)
                        assert_layout(page, width)
                        start = page.get_by_role("button", name="确认启动研究", exact=True)
                        expect(start).to_be_disabled()
                        case_file.write_text(json.dumps("quote_changed"))
                        click_settled(page, page.get_by_role("checkbox"))
                        click_settled(page, start)
                        expect(
                            page.get_by_text("报价或模型已变化，已刷新报价", exact=False)
                        ).to_be_visible()
                        expect(page.get_by_role("textbox", name="股票代码")).to_have_value(
                            "600001.SH"
                        )
                        expect(page.get_by_role("checkbox")).not_to_be_checked()
                        expect(start).to_be_disabled()
                        click_settled(page, page.get_by_role("checkbox"))
                        click_settled(page, start)
                        expect(
                            page.get_by_role("heading", name="价格纪律参考", exact=True)
                        ).to_be_visible(timeout=10000)
                        click_settled(
                            page,
                            page.get_by_role("button", name="查看价格参考 · 演示公司", exact=True),
                        )
                        expect(
                            page.get_by_role("heading", name="量价事实", exact=True)
                        ).to_be_visible()
                        assert_layout(page, width)
                        click_settled(
                            page, page.get_by_role("button", name="返回价格参考", exact=True)
                        )
                        expect(
                            page.get_by_role("heading", name="价格纪律参考", exact=True)
                        ).to_be_visible()
                        click_settled(
                            page, page.get_by_role("button", name="返回来源初筛", exact=True)
                        )
                        expect(page).to_have_url(screen_url)
                        click_settled(
                            page,
                            page.get_by_role("button", name="研究这家公司财报", exact=True).first,
                        )
                        page.get_by_role("textbox", name="股票代码").focus()
                        expect(page.get_by_role("textbox", name="股票代码")).to_have_value(
                            "600001.SH"
                        )
                        expect(
                            page.get_by_text("输出 token 与费用无硬上限", exact=False)
                        ).to_be_visible()
                        assert_layout(page, width)
                        start = page.get_by_role("button", name="确认启动研究", exact=True)
                        expect(start).to_be_disabled()
                        click_settled(page, page.get_by_role("checkbox"))
                        click_settled(page, start)
                        expect(
                            page.get_by_text(re.compile("合成财报报告 · 非真实研究结果"))
                        ).to_be_visible(timeout=10000)
                        expect(
                            page.get_by_role("heading", name="最强反证", exact=True)
                        ).to_be_visible()
                        assert_layout(page, width)
                        financial_url = page.url
                        click_settled(page, page.get_by_role("button", name="依据", exact=True))
                        expect(
                            page.get_by_text("冻结证券范围背景统计", exact=False)
                        ).to_be_visible()
                        expect(
                            page.get_by_text("已归类或扫描：5；尚未扫描：1", exact=False)
                        ).to_be_visible()
                        assert_layout(page, width)
                        click_settled(page, page.get_by_role("button", name=re.compile(r"^\[F1\]")))
                        expect(
                            page.get_by_text(
                                "来源 URL：https://example.invalid/synthetic.pdf", exact=True
                            )
                        ).to_be_visible()
                        assert page.get_by_role("link").count() == 0
                        click_settled(
                            page, page.get_by_role("button", name="返回来源初筛", exact=True)
                        )
                        expect(page).to_have_url(screen_url)
                        if width == 360:
                            for code, label in (
                                ("600002", "收盘至失效线距离 28.57%"),
                                ("600003", "资料核验失败"),
                                ("600004", "没有价格参考"),
                            ):
                                search = page.get_by_role("textbox", name="按代码、名称或行业筛选")
                                click_settled(page, search)
                                search.press("ControlOrMeta+A")
                                search.press_sequentially(code)
                                expect(search).to_have_value(code)
                                expect(
                                    page.get_by_role("button", name="查看价格纪律参考", exact=True)
                                ).to_have_count(1)
                                click_settled(
                                    page,
                                    page.get_by_role("button", name="查看价格纪律参考", exact=True),
                                )
                                click_settled(page, page.get_by_role("checkbox"))
                                click_settled(
                                    page,
                                    page.get_by_role("button", name="确认启动研究", exact=True),
                                )
                                expect(page.get_by_text(label, exact=False)).to_be_visible(
                                    timeout=10000
                                )
                                assert (
                                    page.get_by_text("后续完整交易日收盘在", exact=False).count()
                                    == 0
                                )
                                assert_layout(page, width)
                                click_settled(
                                    page,
                                    page.get_by_role("button", name="返回来源初筛", exact=True),
                                )
                        page.goto(base)
                        page.wait_for_selector("flt-semantics-placeholder")
                        page.evaluate('document.querySelector("flt-semantics-placeholder").click()')
                        expect(
                            page.get_by_role("heading", name="最近研究", exact=True)
                        ).to_be_visible()
                        expect(page.get_by_text("续跑 1 次", exact=False).first).to_be_visible()
                    for case, label in (
                        ("long", "合成的超长公司名称"),
                        ("expired", "价格参考已过期"),
                        ("gap", "资料核验失败"),
                        ("excluded", "未入选"),
                        ("zero", "没有价格参考"),
                        ("missing", "还没有价格参考批次"),
                        ("error", "报告暂时不可读"),
                    ):
                        case_file.write_text(json.dumps(case))
                        # Isolate Flet reconnect state from the previous case's detail route.
                        page.close()
                        page = context.new_page()
                        page.set_viewport_size({"width": 360, "height": 844})
                        page.goto(base)
                        page.wait_for_selector("flt-semantics-placeholder")
                        page.evaluate('document.querySelector("flt-semantics-placeholder").click()')
                        expect(page).to_have_url(base + "/")
                        expect(
                            page.get_by_role("heading", name=re.compile(re.escape(label)))
                        ).to_be_visible()
                        assert_layout(page, 360)
                        if case in ("expired", "gap", "excluded", "zero"):
                            expect(
                                page.get_by_role("heading", name="本批次没有价格参考。", exact=True)
                            ).to_be_visible()
                            expect(
                                page.get_by_text("本批次没有价格参考，请核查资料缺口。", exact=True)
                            ).to_have_count(0)
                        if case == "excluded":
                            expect(page.get_by_text("旧规则距离超过8%", exact=True)).to_be_visible()
                            expect(
                                page.get_by_text("旧规则距离 9.26%", exact=False)
                            ).to_be_visible()
                        if case in ("expired", "gap", "zero", "missing"):
                            assert page.get_by_text(re.compile("11\\.23|11\\.00")).count() == 0
                        if case == "expired":
                            click_settled(
                                page, page.get_by_role("button", name=re.compile("^查看价格参考"))
                            )
                            assert page.get_by_text(re.compile("11\\.23|11\\.00")).count() == 0
                        elif case == "error":
                            click_settled(
                                page, page.get_by_role("button", name="重新读取", exact=True)
                            )
                            expect(
                                page.get_by_role("heading", name="演示公司", exact=True)
                            ).to_be_visible()
                    case_file.write_text(json.dumps("normal"))
                    page.goto(base + "/opportunity/600002.SH")
                    page.wait_for_selector("flt-semantics-placeholder")
                    page.evaluate('document.querySelector("flt-semantics-placeholder").click()')
                    expect(
                        page.get_by_role(
                            "heading", name="本批次没有该公司的价格参考记录。", exact=True
                        )
                    ).to_be_visible()
                    assert page.get_by_text(re.compile("11\\.23|11\\.00")).count() == 0
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.goto(base)
                    page.wait_for_selector("flt-semantics-placeholder")
                    page.evaluate('document.querySelector("flt-semantics-placeholder").click()')
                    expect(
                        page.get_by_role("heading", name="价格纪律参考", exact=True)
                    ).to_be_visible()
                    assert_layout(page, 1280)
                    page.get_by_role("button", name="查看价格参考 · 演示公司", exact=True).focus()
                    page.keyboard.press("Enter")
                    expect(
                        page.get_by_role("heading", name="观察线与失效线", exact=True)
                    ).to_be_visible()
                    page.goto(financial_url)
                    page.wait_for_selector("flt-semantics-placeholder")
                    page.evaluate('document.querySelector("flt-semantics-placeholder").click()')
                    expect(page.get_by_role("heading", name="财报研究", exact=True)).to_be_visible()
                    assert_layout(page, 1280)
                    page.get_by_role("button", name=re.compile(r"^\[F1\]")).focus()
                    page.keyboard.press("Enter")
                    expect(
                        page.get_by_text(
                            "来源 URL：https://example.invalid/synthetic.pdf", exact=True
                        )
                    ).to_be_visible()
                    assert 200 in fonts, "local Chinese font was not loaded"
                    browser.close()
                    print(f"External requests blocked: {sorted(blocked)}")
                print(
                    "Browser passed: 360/390/430px + desktop, list/detail/evidence/settings/logout; long text, expired/gap/empty/missing/read-retry states; keyboard detail/evidence; retired URLs return 404. Synthetic, not a real device."
                )
                return 0
            except Exception:
                print(
                    "Synthetic server diagnostics:", (Path(tmp) / "server.log").read_text()[-5000:]
                )
                raise
            finally:
                server.terminate()
                server.wait(timeout=15)


if __name__ == "__main__":
    raise SystemExit(main())
