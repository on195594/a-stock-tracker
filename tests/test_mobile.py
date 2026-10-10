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
    control.wait_for(state="visible")
    box = control.bounding_box()
    assert box
    center = page.viewport_size["height"] / 2
    page.mouse.move(page.viewport_size["width"] / 2, center)
    page.mouse.wheel(0, box["y"] + box["height"] / 2 - center)
    page.wait_for_function(
        """el => {
            const r = el.getBoundingClientRect();
            const geometry = [r.x, r.y, r.width, r.height].join(',');
            if (el._testGeometry !== geometry) {
                el._testGeometry = geometry;
                el._testStableSince = performance.now();
            }
            return r.top >= 0 && r.bottom <= innerHeight &&
                performance.now() - el._testStableSince >= 250;
        }""",
        arg=control.element_handle(),
        timeout=5000,
    )
    control.click()


def serve_ui_cases(case_file: str) -> None:
    """Test-only data substitution; use the real app, routes and actor checks."""
    import uvicorn

    from a_stock_tracker import app, opportunity_demo
    from a_stock_tracker.research import ScreenError

    assert app.APP_MODE == "demo" and app.HOST == "127.0.0.1"
    original = opportunity_demo.demo_board
    failed = False

    def board():
        nonlocal failed
        case = json.loads(Path(case_file).read_text())
        if case == "missing":
            return None
        if case == "error" and not failed:
            failed = True
            raise ScreenError("synthetic read failure")
        result = original()
        row = result["rows"][0]
        if case == "long":
            row["name"] = "合成的超长公司名称用于验证完整身份换行" * 3
            result["source"] = "仅本地合成资料，未请求行情或模型。" * 8
        elif case == "expired":
            row["expired"] = True
        elif case == "gap":
            row.update(status="gap", reasons=["合成核验失败，不展示价格条件"])
        elif case == "zero":
            result["rows"] = []
        return result

    opportunity_demo.demo_board = board
    uvicorn.run(app.asgi_app, host=app.HOST, port=app.PORT, access_log=False)


def assert_layout(page, width):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for control in page.get_by_role("heading").all() + page.get_by_role("button").all():
        box = control.bounding_box()
        if box and box["width"]:
            assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1, box
    for button in page.get_by_role("button").all():
        box = button.bounding_box()
        if box and box["height"]:
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
                    "from tests.test_mobile import serve_ui_cases; import sys; serve_ui_cases(sys.argv[1])",
                    str(case_file),
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
                    browser = p.chromium.launch(headless=True)
                    context = browser.new_context(viewport={"width": 390, "height": 844})
                    blocked = set()
                    fonts = []

                    def local_only(route):
                        host = urlparse(route.request.url).hostname
                        if host in ("127.0.0.1", "localhost"):
                            route.continue_()
                        else:
                            blocked.add(host)
                            route.abort()

                    context.route("**/*", local_only)
                    page = context.new_page()
                    page.on(
                        "response",
                        lambda response: (
                            fonts.append(response.status)
                            if response.url.endswith("/fonts/NotoSansSC-Regular.otf")
                            else None
                        ),
                    )
                    for width in (360, 390, 430):
                        page.set_viewport_size({"width": width, "height": 844})
                        page.goto(base, timeout=15000)
                        page.wait_for_selector("flt-semantics-placeholder", timeout=15000)
                        page.evaluate('document.querySelector("flt-semantics-placeholder").click()')
                        expect(
                            page.get_by_text("合成演示 · 非真实股票、非AI研究结果")
                        ).to_be_visible()
                        expect(page.get_by_text("等待条件核对 · 不是已触发信号")).to_be_visible()
                        assert_layout(page, width)
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
                            page.get_by_role(
                                "button", name="查看条件与反证 · 演示公司", exact=True
                            ),
                        )
                        expect(page.get_by_text("什么条件才重新评估", exact=True)).to_be_visible()
                        assert_layout(page, width)
                        click_settled(
                            page, page.get_by_role("button", name=re.compile("^核查依据与AI引用"))
                        )
                        expect(page.get_by_text("为什么观察依据：trend、relative")).to_be_visible()
                        page.mouse.move(width / 2, 200)
                        page.mouse.wheel(0, -5000)
                        click_settled(page, page.get_by_role("button", name="账户", exact=True))
                        expect(page.get_by_text("当前模式: demo")).to_be_visible()
                        click_settled(
                            page, page.get_by_role("button", name="返回波段机会", exact=True)
                        )
                        expect(page).to_have_url(base + "/opportunity/600001.SH")
                        click_settled(
                            page, page.get_by_role("button", name="返回波段机会", exact=True)
                        )
                        expect(page).to_have_url(base + "/")
                        page.mouse.move(width / 2, 200)
                        page.mouse.wheel(0, -5000)
                        click_settled(page, page.get_by_role("button", name="账户", exact=True))
                        click_settled(page, page.get_by_role("button", name="退出登录", exact=True))
                        expect(page.get_by_text("仅本地合成演示")).to_be_visible()
                        click_settled(page, page.get_by_role("button", name="进入演示", exact=True))
                        expect(page.get_by_text("波段机会", exact=True)).to_be_visible()
                    for case, label in (
                        ("long", "合成的超长公司名称"),
                        ("expired", "观察已过期"),
                        ("gap", "资料或解读核验失败"),
                        ("zero", "没有有效观察候选"),
                        ("missing", "还没有经过核验的机会报告"),
                        ("error", "报告暂时不可读"),
                    ):
                        case_file.write_text(json.dumps(case))
                        page.set_viewport_size({"width": 360, "height": 844})
                        page.goto(base)
                        page.wait_for_selector("flt-semantics-placeholder")
                        page.evaluate('document.querySelector("flt-semantics-placeholder").click()')
                        expect(
                            page.get_by_role("heading", name=re.compile(re.escape(label)))
                        ).to_be_visible()
                        assert_layout(page, 360)
                        if case in ("expired", "gap", "zero", "missing"):
                            assert page.get_by_text(re.compile("11\\.23|11\\.00")).count() == 0
                        if case == "expired":
                            click_settled(
                                page, page.get_by_role("button", name=re.compile("^查看条件与反证"))
                            )
                            click_settled(
                                page,
                                page.get_by_role("button", name=re.compile("^核查依据与AI引用")),
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
                        page.get_by_role("heading", name="本批次没有该公司的观察记录。", exact=True)
                    ).to_be_visible()
                    assert page.get_by_text(re.compile("11\\.23|11\\.00")).count() == 0
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.goto(base)
                    page.wait_for_selector("flt-semantics-placeholder")
                    page.evaluate('document.querySelector("flt-semantics-placeholder").click()')
                    expect(page.get_by_role("heading", name="波段机会", exact=True)).to_be_visible()
                    assert_layout(page, 1280)
                    page.get_by_role("button", name="查看条件与反证 · 演示公司", exact=True).focus()
                    page.keyboard.press("Enter")
                    expect(
                        page.get_by_role("heading", name="什么条件才重新评估", exact=True)
                    ).to_be_visible()
                    page.get_by_role("button", name=re.compile("^核查依据与AI引用")).focus()
                    page.keyboard.press("Enter")
                    expect(page.get_by_text("为什么观察依据：trend、relative")).to_be_visible()
                    assert 200 in fonts, "local Chinese font was not loaded"
                    browser.close()
                    print(f"External requests blocked: {sorted(blocked)}")
                print(
                    "Browser passed: 360/390/430px + desktop, list/detail/evidence/settings/logout; long text, expired/gap/empty/missing/read-retry states; keyboard detail/evidence; retired URLs return 404. Synthetic, not a real device."
                )
                return 0
            finally:
                server.terminate()
                server.wait(timeout=15)


if __name__ == "__main__":
    raise SystemExit(main())
