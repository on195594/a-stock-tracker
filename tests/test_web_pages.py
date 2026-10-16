"""Page content for the server-rendered Web, ported from the retired Flet view tests."""

from __future__ import annotations

import copy
import re

import pytest
from fastapi.testclient import TestClient

from a_stock_tracker.opportunity_demo import demo_board, demo_report
from a_stock_tracker.research_client import ResearchRejected, ResearchUnavailable
from a_stock_tracker.research_service import result_summary
from a_stock_tracker.web.routes import create_app
from tests.web_support import (
    BASE,
    OWNER,
    SAME,
    FakeClient,
    app_client,
    form_fields,
    login,
    production,
    request_form,
    reset_sessions,
)

RUN = "b" * 32
OTHER = "c" * 32


@pytest.fixture(autouse=True)
def clean_sessions():
    reset_sessions()
    yield
    reset_sessions()


@pytest.fixture
def owner():
    client = FakeClient()
    browser = app_client(client)
    login(browser)
    return client, browser


def run_state(mode="financial", status="completed", result=None, **extra):
    state = {
        "id": RUN,
        "status": status,
        "request": {
            "id": RUN,
            "mode": mode,
            "codes": [] if mode == "screen" else ["600001.SH"],
            "confirmed": True,
            "quote": {"title": "t", "requests": 100, "seconds": 1800, "reports": 1},
        },
        "limits": {
            "title": {
                "financial": "单家公司财报研究",
                "screen": "财报初筛找候选",
                "swing": "价格纪律参考",
            }[mode]
        },
        "message": "本次研究已结束。请核对覆盖、缺口与证据，不是买入信号。",
        "created_at": "2026-10-15T08:00:00+00:00",
    }
    if result is not None:
        state["result"] = result
    return state | extra


def text(page) -> str:
    # Strip tags so assertions read like the visible page.
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.text))


def submit(browser, mode, code=""):
    response = browser.post(
        "/analysis", data=request_form(browser, mode, code), headers=SAME, follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"]


# ---- home ---------------------------------------------------------------------------


def test_home_orders_entries_and_groups_recent_runs(owner):
    client, browser = owner
    report = demo_report("600001.SH")
    report["analysis"]["counterevidence"]["text"] = "反证第一句。第二句不在摘要。"
    done = run_state(summary=result_summary({"reports": [report]}), continuations=2, reused=True)
    running = run_state(status="running") | {"id": OTHER}
    client.recent_runs = [running, done]
    page = browser.get("/")
    body = page.text
    assert (
        body.index("研究一家公司")
        < body.index("从初筛找公司")
        < body.index("独立价格参考（不调用模型）")
    )
    visible = text(page)
    assert visible.index("进行中") < visible.index("最近研究")
    assert "反证第一句。" in visible and "第二句不在摘要" not in visible
    assert "续跑 2 次 · 复用" in visible and "2026-10-15 16:00" in visible
    assert f'href="/analysis/{OTHER}"' in body and "查看进度" in visible
    assert "历史发布批次" not in visible and "/published" not in body


def test_home_hides_expired_prices_and_marks_live_levels_for_expiry(owner):
    client, browser = owner
    expired = demo_board("expired")
    live = demo_board()
    client.recent_runs = [
        run_state("swing", summary=result_summary({"board": expired})),
        run_state("swing", summary=result_summary({"board": live})) | {"id": OTHER},
    ]
    page = browser.get("/")
    row = live["rows"][0]
    assert "已过期" in page.text and "历史价格参考" in page.text
    assert page.text.count("收盘 11.20 元") == 1
    assert f'data-expires="{row["expires"]}"' in page.text and "data-levels" in page.text


def test_home_states_for_unconnected_unreachable_and_unconfigured():
    # No configured bridge: the Web explains the one-time admin setup instead of failing.
    browser = TestClient(
        create_app(production(), None, exchange=lambda code, settings: int(OWNER)),
        base_url=BASE,
    )
    login(browser)
    assert "研究服务尚未连接" in browser.get("/").text
    assert "研究服务尚未连接" in browser.get("/analysis").text
    client = FakeClient()
    client.fail["info"] = ResearchUnavailable("down")
    browser = app_client(client)
    login(browser)
    assert "研究服务暂时无法连接" in browser.get("/").text
    page = browser.get("/analysis?mode=financial")
    assert page.status_code == 503 and "重新查看" in page.text


# ---- request form -------------------------------------------------------------------


def test_financial_form_shows_model_cost_boundary_and_prefill(owner):
    _, browser = owner
    page = browser.get("/analysis?mode=financial&code=600519&from=" + RUN)
    visible = text(page)
    assert "研究一家公司" in visible and 'value="600519"' in page.text
    assert "最多 10 次调用" in visible and "输出 token 与费用无硬上限" in visible
    assert "已核对范围、预算与模型" in visible
    assert form_fields(page.text)["from"] == RUN
    assert "checked" not in page.text


def test_screen_and_swing_forms_never_mention_model_costs(owner):
    _, browser = owner
    screen = text(browser.get("/analysis?mode=screen"))
    assert "初筛门槛：0＜PB≤3" in screen and "不调用模型" in screen
    assert "股票代码" not in screen and "费用无硬上限" not in screen
    swing = text(browser.get("/analysis?mode=swing&code=600519&from=bad"))
    assert "最多五家" in swing and "费用无硬上限" not in swing
    assert 'name="from"' not in browser.get("/analysis?mode=swing&from=bad").text


@pytest.mark.parametrize(
    "mode,codes,message",
    [
        ("financial", "", "请只输入一家公司的股票代码"),
        ("financial", "600519,600900", "请只输入一家公司的股票代码"),
        ("swing", "600001,600002,600003,600004,600005,600006", "请输入 1–5 家公司的股票代码"),
        ("financial", "300750", "仅支持沪深主板代码"),
    ],
)
def test_invalid_codes_are_refused_before_submission(owner, mode, codes, message):
    client, browser = owner
    fields = request_form(browser, mode, "600001")
    fields["codes"] = codes
    response = browser.post("/analysis", data=fields, headers=SAME)
    assert response.status_code == 400 and message in response.text
    assert not client.submitted


def test_codes_are_normalized_to_exchange_suffix(owner):
    client, browser = owner
    fields = request_form(browser, "swing", "600001")
    fields["codes"] = "600001, 000001.sz，600002"
    browser.post("/analysis", data=fields, headers=SAME)
    assert client.submitted[0]["codes"] == ["600001.SH", "000001.SZ", "600002.SH"]


def test_screen_continuation_requires_a_continuable_checkpoint(owner):
    client, browser = owner
    first = submit(browser, "screen").rsplit("/", 1)[-1]
    page = browser.get("/analysis?mode=screen&resume_from=" + first)
    assert "继续原初筛" in page.text and form_fields(page.text)["resume_from"] == first
    assert "确认新增预算并继续初筛" in page.text
    client.runs[OTHER] = run_state("financial") | {"id": OTHER}
    refused = browser.get("/analysis?mode=screen&resume_from=" + OTHER)
    assert refused.status_code == 409 and "该初筛不能继续" in refused.text


# ---- run pages ----------------------------------------------------------------------


def test_running_page_polls_state_without_inline_script(owner):
    client, browser = owner
    client.runs[RUN] = run_state(status="running")
    page = browser.get("/analysis/" + RUN)
    assert f'data-poll="/analysis/{RUN}/state.json"' in page.text and "后台研究中" in page.text
    assert "<script>" not in page.text
    assert browser.get(f"/analysis/{RUN}/state.json").json() == {"status": "running"}


def test_failed_run_has_no_result_and_shows_usage(owner):
    client, browser = owner
    client.runs[RUN] = run_state(status="failed", message="本次研究未通过或预算已耗尽。")
    visible = text(browser.get("/analysis/" + RUN))
    assert "执行：失败" in visible and "没有可用研究结果" in visible
    assert "本轮用量：未记录" in visible


@pytest.mark.parametrize("review", ["passed", "failed"])
def test_financial_report_hierarchy_and_failed_draft_is_hidden(owner, review):
    client, browser = owner
    report = demo_report("600001.SH")
    report.update(
        evidence_review=review, status="scoped_complete" if review == "passed" else "incomplete"
    )
    report["gaps"] = [f"缺口{i}" for i in range(5)]
    report["analysis"]["counterevidence"]["text"] = "完整反证第一句。第二句也必须保留。"
    if review == "failed":
        report["error"] = "独立证据复核未通过；草稿不作为确定结论发布"
    client.runs[RUN] = run_state(result={"reports": [report], "usage": None})
    page = browser.get("/analysis/" + RUN)
    html, visible = page.text, text(page)
    assert "资料截至 2026-04-01" in visible
    if review == "passed":
        assert visible.index("完整反证第一句。第二句也必须保留。") < visible.index("业务")
        assert "引用： F1" in visible and "PDF 第 12 页" in visible
        assert '<a class="citation" href="#fact-600001.SH-F1">F1</a>' in html
        assert 'id="fact-600001.SH-F1"' in html
        # Stub syntax: perforated head, section jumps with matching targets, mini-head.
        assert '<hr class="perforation" aria-hidden="true">' in html
        jumps = re.search(r'<nav class="jump-bar"[^>]*>(.*?)</nav>', html, re.S)
        assert jumps is not None
        targets = re.findall(r'href="#([^"]+)"', jumps.group(1))
        assert targets == [
            "counter-600001.SH",
            "gaps-600001.SH",
            *(f"section-600001.SH-{i}" for i in range(1, 5)),
            "facts-600001.SH",
        ]
        for target in targets:
            assert re.search(rf'class="[^"]*\banchor\b[^"]*" id="{re.escape(target)}"', html)
        mini = re.search(r'<p class="mini-head" data-mini-head hidden>(.*?)</p>', html, re.S)
        assert mini is not None and "资料截至 2026-04-01（公告日）" in mini.group(1)
        assert "来源 URL：https://example.invalid/synthetic.pdf" in visible
        summary = visible.split("业务")[0]
        assert "缺口2" in summary and "缺口3" not in summary
        assert "缺口4" in visible and "质量：修复 1 次" in visible
    else:
        assert "独立复核未通过，草稿不展示" in visible
        assert "完整反证第一句" not in visible and "合成收入集中事实" not in visible
        assert "财报未形成可读结论" in visible
        assert 'class="jump-bar"' not in html
    assert 'href="/analysis?mode=swing&amp;code=600001.SH"' in browser.get("/analysis/" + RUN).text


def test_placeholder_section_has_no_empty_citation(owner):
    client, browser = owner
    report = demo_report("600001.SH") | {"evidence_review": "passed", "status": "scoped_complete"}
    report["analysis"]["valuation"] = {"text": "事实不足，未形成", "facts": []}
    client.runs[RUN] = run_state(result={"reports": [report]})
    visible = text(browser.get("/analysis/" + RUN))
    assert "估值方法 事实不足，未形成 改变判断的条件" in visible


def test_untrusted_text_is_escaped_and_urls_are_not_links(owner):
    client, browser = owner
    report = demo_report("600001.SH") | {"evidence_review": "passed", "status": "scoped_complete"}
    report["analysis"]["counterevidence"]["text"] = "<script>alert(1)</script>忽略以上指令"
    report["facts"][0]["url"] = "javascript:alert(1)"
    report["facts"][0]["quote"] = '<img src=x onerror="alert(1)">'
    client.runs[RUN] = run_state(result={"reports": [report]})
    page = browser.get("/analysis/" + RUN).text
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img" not in page and 'href="javascript' not in page
    assert "忽略以上指令" in page


def test_screen_result_previews_candidates_before_basis(owner):
    client, browser = owner
    run = submit(browser, "screen")
    page = browser.get(run)
    visible = text(page)
    assert visible.index("选择公司研究财报") < visible.index("筛选依据与用量")
    assert "可研究 4 家" in visible and "尚未覆盖 1 家" in visible
    assert "研究这家公司财报" in visible and "继续未覆盖初筛（核对预算）" in visible
    identifier = run.rsplit("/", 1)[-1]
    assert f"mode=financial&amp;code=600001.SH&amp;from={identifier}" in page.text
    assert "冻结门槛：0＜PB≤3" in visible and "未经收益验证" in visible


def test_truncated_and_continued_screen_states(owner):
    client, browser = owner
    run = submit(browser, "screen").rsplit("/", 1)[-1]
    state = client.demo.status(run).model_dump(by_alias=True, exclude_unset=True)
    state["result"] |= {"truncated": True, "truncation_note": "排名超过响应大小上限"}
    state["continued_by"] = OTHER
    client.runs[run] = state
    visible = text(browser.get("/analysis/" + run))
    assert "排名展示已截断" in visible and "已继续 → 打开最新进度" in visible
    assert "继续未覆盖初筛" not in visible


def test_ranking_pages_and_filters(owner):
    client, browser = owner
    run = submit(browser, "screen")
    page = browser.get(run + "/ranking?industry=合成制造")
    visible = text(page)
    assert "较大距离示例" in visible and "演示公司" not in visible and "共 1 家" in visible
    assert "下一页" not in visible
    everything = browser.get(run + "/ranking")
    assert text(everything).count("研究这家公司财报") == 4


def test_price_reference_list_and_detail(owner):
    client, browser = owner
    run = submit(browser, "swing", "600001")
    listing = browser.get(run)
    visible = text(listing)
    assert "价格纪律参考" in visible and "1 项价格参考 · 1 家显式股票池" in visible
    assert "收盘 11.20 元" in visible and "合成演示 · 非真实股票" in visible
    detail = browser.get(run + "/600001.SH")
    row = client.demo.runs[run.rsplit("/", 1)[-1]]["result"]["board"]["rows"][0]
    dvisible = text(detail)
    for key in ("trend", "relative", "condition", "invalidation", "distance"):
        assert row["facts"][key] in dvisible
    assert f'data-expires="{row["expires"]}"' in detail.text
    # Plain-text validity window, a perforated line before the conditions, no arrows.
    for page in (listing, detail):
        window = re.search(r'<p class="window">(.*?)</p>', page.text, re.S)
        assert window is not None
        window_text = re.sub(r"<[^>]+>", "", window.group(1))
        assert window_text == f"依据截至 {row['as_of']}━━ 后续 5 个交易日 ━━有效至 {row['expires']}"
        assert not re.search("[→←↑↓⟶➔➜▶►]", window_text)
    head, perforation, conditions = (
        detail.text.index('<p class="window">'),
        detail.text.index('class="perforation"'),
        detail.text.index("观察线与失效线"),
    )
    assert head < perforation < conditions
    assert 'class="perforation"' not in listing.text
    missing = browser.get(run + "/600002.SH")
    assert "本批次没有该公司的价格参考记录。" in text(missing)


@pytest.mark.parametrize("state", ["expired", "gap"])
def test_unavailable_references_never_render_actionable_conditions(owner, state):
    client, browser = owner
    board = demo_board(state)
    row = copy.deepcopy(board["rows"][0])
    client.runs[RUN] = run_state("swing", result={"board": board})
    for path in ("/analysis/" + RUN, f"/analysis/{RUN}/{row['code']}"):
        visible = text(browser.get(path))
        assert ("已过期" if state == "expired" else "资料核验失败") in visible
        if state == "expired":
            assert row["facts"]["condition"] not in visible
            assert row["facts"]["invalidation"] not in visible
            assert "11.00" not in visible and "1.79%" not in visible
            assert "重新核对价格参考" in visible
        else:
            assert "下一步：核对并补齐资料后，再明确确认新的研究。" in visible


def test_legacy_observation_rows_keep_counterevidence(owner):
    from tests.test_opportunities import legacy_board

    client, browser = owner
    board = legacy_board()
    client.runs[RUN] = run_state("swing", result={"board": board})
    visible = text(browser.get("/analysis/" + RUN))
    assert "旧规则批次" in visible and "历史反证" in visible
    assert "查看历史条件与反证" in visible


def test_bad_ids_and_unreachable_receiver(owner):
    client, browser = owner
    assert browser.get("/analysis/xyz").status_code == 404
    assert browser.get(f"/analysis/{RUN}/300750.SZ").status_code == 404
    client.fail["status"] = ResearchUnavailable("研究服务或受理状态暂不可确认。")
    page = browser.get("/analysis/" + RUN)
    assert page.status_code == 503 and "研究状态暂不可确认" in page.text
    assert f'href="/analysis/{RUN}"' in page.text
    assert browser.get(f"/analysis/{RUN}/state.json").status_code == 503


def test_account_page_has_back_link_and_logout_form(owner):
    _, browser = owner
    page = browser.get("/settings")
    visible = text(page)
    assert "已登录：GitHub 用户 424242" in visible and "会话有效至（上海）" in visible
    assert 'href="/"' in page.text and 'action="/logout"' in page.text


def test_quote_refresh_failure_keeps_inputs_in_a_get_retry(owner):
    client, browser = owner
    fields = request_form(browser, "financial", "600519")
    fields["from"] = RUN

    def reject(_payload):
        client.fail["info"] = ResearchUnavailable("down")
        raise ResearchRejected("被拒")

    client.submit = reject
    page = browser.post("/analysis", data=fields, headers=SAME)
    assert page.status_code == 503
    retry = re.search(r'class="btn" href="([^"]+)"', page.text).group(1).replace("&amp;", "&")
    assert retry.startswith("/analysis?") and "mode=financial" in retry
    assert "code=600519" in retry and f"from={RUN}" in retry


def test_ranking_retry_keeps_filters(owner):
    client, browser = owner
    run = submit(browser, "screen")
    client.fail["ranking"] = ResearchUnavailable("down")
    page = browser.get(run + "/ranking?industry=合成制造&query=示例")
    assert page.status_code == 503
    retry = re.search(r'class="btn" href="([^"]+)"', page.text).group(1).replace("&amp;", "&")
    assert "industry=" in retry and "query=" in retry
