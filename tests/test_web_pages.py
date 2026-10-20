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
    report["analysis"]["summary"]["text"] = "需要谨慎：摘要一句。"
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
    # Running work is pinned above the entries and listed once, not again as a card.
    assert visible.index("进行中") < visible.index("研究一家公司")
    assert body.count(f'href="/analysis/{OTHER}"') == 1 and 'aria-label="查看进度' in body
    assert visible.index("进行中") < visible.index("我研究过的公司 演示公司 · 600001.SH")
    assert 'href="/stock/600001.SH"' in body
    assert "需要谨慎：摘要一句。" in visible
    assert "披露期：2025-12-31（公告 2026-04-01）" in visible
    assert "历史发布批次" not in visible and "/published" not in body
    # Chain details stay in the per-type records.
    records = text(browser.get("/?view=financial"))
    assert "续跑 2 次 · 复用" in records and "2026-10-15 16:00" in records


def test_home_company_cards_fold_failed_superseded_and_expired_results(owner):
    client, browser = owner
    readable = demo_report("600001.SH")
    failed = demo_report("600001.SH") | {"evidence_review": "failed", "status": "incomplete"}
    never = demo_report("000001.SZ") | {
        "name": "从未成功",
        "evidence_review": "failed",
        "status": "incomplete",
    }
    expired, live = demo_board("expired"), demo_board()
    client.recent_runs = [
        # newest first: a failed retry, then the readable report it must not hide
        run_state(summary=result_summary({"reports": [failed]})) | {"id": "1" * 32},
        run_state(summary=result_summary({"reports": [readable]})) | {"id": "2" * 32},
        run_state(summary=result_summary({"reports": [never]})) | {"id": "3" * 32},
        run_state("swing", summary=result_summary({"board": live})) | {"id": "4" * 32},
        run_state("swing", summary=result_summary({"board": expired})) | {"id": "5" * 32},
    ]
    page = browser.get("/")
    visible = text(page)
    assert visible.count("演示公司 · 600001.SH") == 1
    assert "需要谨慎（合成示例）" in visible
    assert 'href="/analysis/' + "1" * 32 not in page.text  # failed retry folded away
    assert "上次财报研究未能生成结论" in visible and 'href="/analysis/' + "3" * 32 in page.text
    # Only the newest price row shows, and only while live.
    assert page.text.count("收盘 11.20 元") == 1 and "价格参考已过期" not in visible
    assert "自动收起" in visible


def test_home_overview_reads_every_mode_concurrently_and_links_tabs(owner):
    client, browser = owner
    client.recent_runs = [
        run_state(mode) | {"id": identifier}
        for mode, identifier in (("financial", RUN), ("screen", OTHER), ("swing", "d" * 32))
    ]
    page = browser.get("/")
    assert {(c["mode"], c["limit"]) for c in client.recent_calls} == {
        (None, 5),
        ("financial", 20),
        ("screen", 1),
        ("swing", 20),
    }
    tabs = re.search(r'<nav class="tabs"[^>]*>(.*?)</nav>', page.text, re.S).group(1)
    assert re.findall(r'href="([^"]+)"', tabs) == [
        "/?view=overview",
        "/?view=financial",
        "/?view=screen",
        "/?view=swing",
    ]
    assert '<a href="/?view=overview" aria-current="page">公司</a>' in tabs
    visible = text(page)
    assert visible.index("我研究过的公司") < visible.index("最近一次初筛")
    assert f'href="/analysis/{OTHER}"' in page.text
    # A failed screen has no candidates to offer; it collapses with other failures.
    client.recent_runs[1] = run_state("screen", status="failed") | {"id": OTHER}
    assert "最近一次初筛" not in text(browser.get("/"))


def test_home_mode_tab_lists_only_that_mode_and_pages_by_cursor(owner):
    client, browser = owner
    swings = [run_state("swing") | {"id": f"{i:032x}"} for i in range(10)]
    client.recent_runs = [run_state("financial"), *swings]
    page = browser.get("/?view=swing")
    visible = text(page)
    assert '<a href="/?view=swing" aria-current="page">价格记录</a>' in page.text
    assert "财报研究 600001.SH" not in visible and page.text.count('aria-label="查看结果') == 10
    assert f'href="/?view=swing&amp;cursor={swings[-1]["id"]}"' in page.text
    assert {"mode": "swing", "code": None, "limit": 10, "cursor": None} in client.recent_calls
    client.recent_calls.clear()
    older = browser.get(f"/?view=swing&cursor={swings[-1]['id']}")
    assert "价格记录 · 更早记录" in text(older) and 'href="/?view=swing"' in older.text
    # Only the tab query pages; the global running-work query never takes the cursor.
    assert {(c["mode"], c["cursor"]) for c in client.recent_calls} == {
        (None, None),
        ("swing", swings[-1]["id"]),
    }
    # Unknown views fall back to the overview; malformed cursors are never forwarded.
    client.recent_calls.clear()
    assert 'aria-current="page">公司' in browser.get("/?view=../x&cursor=zz").text
    assert all(c["cursor"] is None for c in client.recent_calls)
    browser.get("/?view=screen&cursor=not-a-run")
    assert all(c["cursor"] is None for c in client.recent_calls)


def test_home_mode_tab_rejected_cursor_and_unreachable(owner):
    client, browser = owner
    client.fail["recent"] = ResearchRejected("分页游标不在当前研究列表中")
    page = browser.get("/?view=financial&cursor=" + RUN)
    assert page.status_code == 400 and "分页已失效" in page.text
    assert 'href="/?view=financial"' in page.text
    client.fail["recent"] = ResearchUnavailable("down")
    assert "研究服务暂时无法连接" in browser.get("/?view=financial").text


def test_home_hides_expired_prices_and_marks_live_levels_for_expiry(owner):
    client, browser = owner
    expired = demo_board("expired")
    live = demo_board()
    client.recent_runs = [
        run_state("swing", summary=result_summary({"board": expired})),
        run_state("swing", summary=result_summary({"board": live})) | {"id": OTHER},
    ]
    page = browser.get("/?view=swing")
    row = live["rows"][0]
    assert "已过期" in page.text and "价格参考已过期" in page.text
    assert "原有效至" in page.text
    assert page.text.count("收盘 11.20 元") == 1
    # The live tag and levels share one expiring block, so both swap at expiry.
    block = re.search(
        rf'<div class="summary" data-expires="{row["expires"]}">(.*?)</div>', page.text, re.S
    )
    assert block is not None
    assert '<span class="tag" data-levels>有效期内</span>' in block.group(1)
    assert '<p class="lines" data-levels>收盘 11.20 元' in block.group(1)
    assert block.group(1).count("data-expired hidden") == 2
    assert '<span class="tag caution">已过期</span>' in page.text


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
    assert "原因见上方执行说明" in visible  # the failure note leads the page
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
    assert "演示公司 · 600001.SH" in visible
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
        assert (
            "缺口4" in visible
            and "质量：机械校验通过 1 条 / 语义复核通过 1 条 / 修复 1 次" in visible
        )
    else:
        assert "未能生成结论：独立复核未通过" in visible
        assert "完整反证第一句" not in visible and "合成收入集中事实" not in visible
        assert "看价格参考" in visible
        assert 'class="jump-bar"' not in html
    assert 'href="/analysis?mode=swing&amp;code=600001.SH"' in browser.get("/analysis/" + RUN).text


def test_placeholder_section_has_no_empty_citation(owner):
    client, browser = owner
    report = demo_report("600001.SH") | {"evidence_review": "passed", "status": "scoped_complete"}
    report["analysis"]["valuation"] = {"text": "事实不足，未形成", "facts": []}
    client.runs[RUN] = run_state(result={"reports": [report]})
    visible = text(browser.get("/analysis/" + RUN))
    assert "估值 资料不足，本次没有形成判断。 什么会改变结论" in visible


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
    assert "可研究 4 家" in visible and "还有 1 家没扫描" in visible
    assert 'aria-label="研究这家公司财报 · 演示公司"' in page.text
    assert "继续未覆盖初筛（核对预算）" in visible
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
    assert everything.text.count('aria-label="研究这家公司财报') == 4


def test_price_reference_list_and_detail(owner):
    client, browser = owner
    run = submit(browser, "swing", "600001")
    listing = browser.get(run)
    visible = text(listing)
    assert "价格参考" in visible and "1 / 1 家得到价格参考" in visible
    assert "走势" in visible and "跌破 11.00 元" in visible
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
        detail.text.index("关键价位怎么来的"),
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


# ---- company dossier ----------------------------------------------------------------


def archived(run_id, finished_at, **extra):
    return {
        "run_id": run_id,
        "code": "600001.SH",
        "name": "演示公司",
        "finished_at": finished_at,
        "status": "scoped_complete",
        "documents": [{"period": "2025-12-31", "announced": "2026-04-01"}],
        "omitted_pages": 0,
        "gap_count": 2,
    } | extra


def test_company_dossier_shows_latest_report_price_and_archive(owner):
    client, browser = owner
    client.reports = {
        "code": "600001.SH",
        "reports": [
            archived(RUN, "2026-10-15T08:00:00+00:00"),
            archived(OTHER, "2026-09-01T08:00:00+00:00", omitted_pages=3),
        ],
    }
    live = demo_board()
    client.recent_runs = [
        run_state("swing", summary=result_summary({"board": live})) | {"id": "d" * 32},
        run_state("financial", summary=result_summary({"reports": []})),
    ]
    page = browser.get("/stock/600001.SH")
    visible = text(page)
    assert page.status_code == 200 and "<h1>演示公司</h1>" in page.text
    assert {"mode": "swing", "code": "600001.SH", "limit": 1, "cursor": None} in (
        client.recent_calls
    )
    assert {"mode": "financial", "code": "600001.SH", "limit": 1, "cursor": None} in (
        client.recent_calls
    )
    assert visible.index("最新研究 · 2026-10-15 16:00") < visible.index("价格参考")
    assert "研究完成" in visible and "待补资料 2 条" in visible
    assert f'href="/analysis/{RUN}#report-600001.SH"' in page.text
    assert 'href="/analysis?mode=financial&amp;code=600001.SH"' in page.text
    # Price reference: live levels in an expiring block plus the detail link.
    row = live["rows"][0]
    assert f'data-expires="{row["expires"]}"' in page.text and "收盘 11.20 元" in visible
    assert f'href="/analysis/{"d" * 32}/600001.SH"' in page.text
    assert 'href="/analysis?mode=swing&amp;code=600001.SH"' in page.text
    # The newest financial run left nothing readable, so it is surfaced with its reason.
    assert "最近一次财报研究没有可读结论" in visible
    assert visible.index("价格参考") < visible.index("更早的研究（1 份）")
    assert f'href="/analysis/{OTHER}#report' in page.text


def test_company_dossier_empty_and_running_states(owner):
    client, browser = owner
    client.recent_runs = []
    visible = text(browser.get("/stock/600001.SH"))
    assert "还没有可读的研究报告。" in visible and "研究这家公司" in visible
    assert "还没有算过价格参考。" in visible and "计算价格参考（不调用模型）" in visible
    assert "更早的研究" not in visible
    client.recent_runs = [
        run_state("financial", status="running"),
        run_state("swing", status="running") | {"id": OTHER},
    ]
    page = browser.get("/stock/600001.SH")
    visible = text(page)
    # Running work is shown with its progress link; no second request is offered.
    assert "财报研究进行中" in visible and "价格参考计算中" in visible
    assert "mode=financial" not in page.text and "mode=swing" not in page.text
    assert f'href="/analysis/{RUN}"' in page.text and f'href="/analysis/{OTHER}"' in page.text


def test_company_dossier_ignores_runs_for_other_codes_and_modes(owner):
    client, browser = owner
    # A receiver that ignored the filters must not leak another company into the dossier.
    other = run_state("swing", summary=result_summary({"board": demo_board()}))
    other["request"]["codes"] = ["600002.SH"]
    client.recent = lambda **_: [other, run_state("screen")]
    client.reports = {"code": "600001.SH", "reports": [archived(RUN, None, code="600002.SH")]}
    visible = text(browser.get("/stock/600001.SH"))
    assert "还没有算过价格参考。" in visible and "还没有可读的研究报告。" in visible


def test_company_dossier_validates_codes_and_reports_unreachable(owner):
    client, browser = owner
    assert browser.get("/stock/300750.SZ").status_code == 404
    assert browser.get("/stock/abc").status_code == 404
    moved = browser.get("/stock/600001", follow_redirects=False)
    assert moved.status_code == 303 and moved.headers["location"] == "/stock/600001.SH"
    assert browser.get("/stock/000001.sz", follow_redirects=False).headers["location"] == (
        "/stock/000001.SZ"
    )
    client.fail["find_reports"] = ResearchUnavailable("down")
    page = browser.get("/stock/600001.SH")
    assert page.status_code == 503 and "公司档案暂不可读取" in page.text
    assert 'class="btn" href="/stock/600001.SH"' in page.text


def test_financial_form_flags_an_existing_report_without_depending_on_it(owner):
    client, browser = owner
    assert "该公司已有研报" not in browser.get("/analysis?mode=financial&code=600001").text
    run = submit(browser, "financial", "600001").rsplit("/", 1)[-1]
    page = browser.get("/analysis?mode=financial&code=600001")
    assert "该公司已有研报" in page.text and f'href="/analysis/{run}#report-600001.SH"' in page.text
    assert "该公司已有研报" not in browser.get("/analysis?mode=swing&code=600001").text
    # A failed or malformed report lookup is only a missing hint; the confirmation form still renders.
    client.fail["find_reports"] = ResearchUnavailable("down")
    page = browser.get("/analysis?mode=financial&code=600001")
    assert page.status_code == 200 and "该公司已有研报" not in page.text
    assert 'name="confirmed"' in page.text
    client.fail.clear()
    client.reports = {"code": "600001.SH", "reports": None}
    page = browser.get("/analysis?mode=financial&code=600001")
    assert page.status_code == 200 and "该公司已有研报" not in page.text
    assert 'name="confirmed"' in page.text


def test_report_screen_ranking_and_price_pages_link_codes_to_the_dossier(owner):
    client, browser = owner
    dossier = 'href="/stock/600001.SH">600001.SH · 公司档案</a>'
    financial = submit(browser, "financial", "600001")
    assert dossier in browser.get(financial).text
    screen = submit(browser, "screen")
    assert dossier in browser.get(screen).text
    assert dossier in browser.get(screen + "/ranking").text
    swing = submit(browser, "swing", "600001")
    assert dossier in browser.get(swing).text
    assert dossier in browser.get(swing + "/600001.SH").text
    # The synthetic client lists its own reports and price runs in the dossier.
    visible = text(browser.get("/stock/600001.SH"))
    assert "合成示例，未进行真实复核" in visible or "合成报告" in visible
    assert "收盘 11.20 元" in visible
