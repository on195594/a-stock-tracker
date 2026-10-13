"""Read research coverage and reports without inventing candidates or hidden costs."""

from __future__ import annotations

from typing import Any

import flet as ft

from a_stock_tracker.disclosures import report_summary_status
from a_stock_tracker.opportunity_view import (
    heading,
    local_time,
    notice,
    opportunity_view,
    paragraph,
    price_summary,
)
from a_stock_tracker.research import finite_number

LABELS = {
    "running": "执行中",
    "completed": "已结束",
    "partial": "已结束",
    "failed": "失败",
    "interrupted": "中断",
}


def source_times_text(value: Any) -> str:
    if not isinstance(value, dict):
        return local_time(value)
    labels = {
        "stock_basic_acquired_at": "证券基本资料",
        "daily_basic_acquired_at": "每日估值资料",
    }
    entries = [f"{label}：{local_time(value.get(key))}" for key, label in labels.items()]
    entries.extend(
        f"{key}：{local_time(stamp)}" for key, stamp in value.items() if key not in labels
    )
    return "\n".join(entries)


def roe_text(value: Any) -> str:
    number = finite_number(value)
    return f"{number:.2f}%" if number is not None else "未记录"


def report_dates(report) -> str:
    docs = report.get("documents", [])
    if not docs:
        return "资料日期：未记录"
    latest = max(d["announced"] for d in docs)
    return f"资料截至 {latest}（公告日）\n" + "；".join(
        f"报告期 {d['period']} / 公告 {d['announced']}" for d in docs
    )


def readable_report(report) -> bool:
    return report.get("evidence_review") == "passed" or report.get("status") == "synthetic"


def report_controls(report) -> list[ft.Control]:
    readable = readable_report(report)
    analysis = report.get("analysis", {}) if readable else {}
    counter = analysis.get("counterevidence", {})
    summary: list[ft.Control] = [
        paragraph(report_summary_status(report)),
        paragraph(report_dates(report)),
    ]
    if counter:
        summary.extend(
            [
                heading("最强反证", size=18),
                paragraph(counter["text"]),
                paragraph("引用：" + "、".join(counter["facts"])),
            ]
        )
    gaps = report.get("gaps", [])
    if gaps:
        summary.extend([heading("主要缺口", size=18), *[paragraph(g) for g in gaps[:3]]])
    if report.get("error"):
        summary.append(paragraph(report["error"]))
    controls: list[ft.Control] = [notice("报告 · " + report["code"], "财报研究摘要", extra=summary)]
    clues = report.get("clues", {})
    if any(v is not None for v in clues.values()):
        controls.append(
            paragraph(
                f"入围线索：行业 {clues.get('industry')}；估值日 {clues.get('valuation_date')}；"
                f"PB {clues.get('pb')}；历史年度平均 ROE {roe_text(clues.get('roe_mean'))}。仅为同业研究顺序。"
            )
        )
    for key, title in (
        ("business", "业务"),
        ("financial", "财务"),
        ("valuation", "估值方法"),
        ("change_conditions", "改变判断的条件"),
    ):
        if key in analysis:
            item = analysis[key]
            controls.extend(
                [
                    heading(title, size=18),
                    paragraph(item["text"]),
                    paragraph("引用：" + "、".join(item["facts"])),
                ]
            )
    if readable and report.get("facts"):
        controls.append(heading("原文事实"))
        for fact in report["facts"]:
            controls.append(
                ft.ExpansionTile(
                    title=ft.Text(f"[{fact['id']}] {fact['claim']}"),
                    controls=[
                        paragraph(
                            f"来源：{fact['title']} · PDF 第 {fact['page']} 页\n报告期 {fact['period']}；公告 {fact['announced']}"
                        ),
                        paragraph(fact["quote"]),
                        paragraph("来源 URL：" + fact["url"]),
                    ],
                    controls_padding=16,
                )
            )
    if gaps:
        controls.append(
            ft.ExpansionTile(
                title=ft.Text("全部缺口与范围"),
                controls=[paragraph(g) for g in gaps],
                controls_padding=16,
            )
        )
    controls.append(
        paragraph(
            f"未纳入分析的原文：{report.get('omitted_pages', 0)} 页。仅定期报告范围内研究，不是投资结论保证。"
        )
    )
    return controls


def recent_controls(runs, open_route) -> list[ft.Control]:
    controls: list[ft.Control] = []
    for title, active in (("进行中", True), ("最近研究", False)):
        controls.append(heading(title, size=22))
        items = [r for r in runs if (r["status"] == "running") == active]
        if not items:
            controls.append(
                paragraph("暂无进行中的研究。" if active else "暂无研究记录，可开始选股与研究。")
            )
        for item in items:
            chain = f" · 续跑 {item['continuations']} 次" if item.get("continuations") else ""
            if item.get("reused") or item.get("reused_from"):
                chain += " · 复用"
            result = item.get("summary", {})
            summaries = []
            for report in result.get("reports", []):
                counter = (
                    report.get("analysis", {}).get("counterevidence", {}).get("text", "")
                    if readable_report(report)
                    else ""
                )
                summaries.append(
                    paragraph(
                        f"{report['code']} · {report_summary_status(report)}\n{report_dates(report)}"
                        + ("\n最强反证：" + counter.split("。", 1)[0] + "。" if counter else "")
                    )
                )
            for row in result.get("board", {}).get("rows", []):
                summaries.append(paragraph(row["code"] + "\n" + price_summary(row)))
            controls.append(
                notice(
                    item["limits"]["title"]
                    + " · "
                    + (",".join(item["request"]["codes"]) or "全市场初筛"),
                    f"{LABELS.get(item['status'], '状态待核查')}{chain} · {local_time(item.get('created_at'))}",
                    extra=[
                        *summaries,
                        ft.TextButton(
                            ft.Text("查看进度与结果", semantics_label="查看研究 · " + item["id"]),
                            on_click=open_route("/analysis/" + item["id"]),
                            height=48,
                        ),
                    ],
                )
            )
    return controls


def usage_text(usage, mode: str | None = None) -> str:
    if not usage:
        return "本轮用量：未记录"

    def recorded(key):
        return usage.get(key) if usage.get(key) is not None else "未记录"

    reason = {
        "completed": "完成本轮",
        "reused": "复用已有结果，未执行采集",
        "request_budget": "请求预算用尽",
        "time_budget": "时间预算用尽",
        "error": "执行异常",
    }.get(usage.get("stop_reason"), "未记录")
    coverage = (
        "冻结证券范围背景统计（非本次单家公司研究进度）：\n"
        f"本轮新增财报扫描：{recorded('newly_covered')}；已归类或扫描：{recorded('covered')}；尚未扫描：{recorded('remaining')}\n"
        "已归类或扫描包含预先排除和资料缺口；尚未扫描包含本次指定范围以外的证券。"
        if mode == "financial"
        else f"本轮新增覆盖：{recorded('newly_covered')}；累计覆盖：{recorded('covered')}；剩余未覆盖：{recorded('remaining')}"
    )
    return (
        f"本轮请求：{recorded('requests')} / {recorded('request_limit')} 次；耗时：{recorded('seconds_used')} 秒\n"
        f"停止原因：{reason}\n{coverage}"
    )


RANK_PREVIEW = 3
SEARCH_LIMIT = 30


def ranking_controls(groups, identifier, open_route, is_current) -> list[ft.Control]:
    # A full screen has ~1000 ranked rows; build controls lazily to keep mobile sessions light.
    def row_control(row: dict[str, Any]) -> ft.Control:
        code = row["code"]
        return ft.Column(
            [
                heading(f"{row['name']} · {code}"),
                paragraph(
                    f"PB（倍）：{row.get('pb', '未记录')}；历史年度平均 ROE：{roe_text(row.get('roe_mean'))}\n"
                    + (
                        f"总市值：{row['total_mv'] / 10000:g}亿元\n"
                        if row.get("total_mv") is not None
                        else ""
                    )
                    + f"平均名次：{row.get('order', '未记录')}；PB 名次：{row.get('pb_rank', '未记录')}；ROE 名次：{row.get('roe_rank', '未记录')}"
                ),
                ft.Button(
                    "研究这家公司财报",
                    on_click=open_route(f"/analysis?mode=financial&code={code}&from={identifier}"),
                    height=48,
                ),
                ft.TextButton(
                    "查看价格纪律参考",
                    on_click=open_route(f"/analysis?mode=swing&code={code}&from={identifier}"),
                    height=48,
                ),
                ft.Divider(),
            ],
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def show_all(group, tile):
        def handler(e):
            if is_current():
                tile.controls = [row_control(r) for r in group["ranking"]]
                e.page.update()

        return handler

    tiles: list[ft.Control] = []
    for index, group in enumerate(groups):
        tile = ft.ExpansionTile(
            title=ft.Text(group["industry"]),
            subtitle=ft.Text(
                f"{len(group['ranking'])} 家 · 比较年度：" + "、".join(group["periods"])
            ),
            expanded=index < 3,
            controls_padding=12,
        )
        tile.controls = [row_control(r) for r in group["ranking"][:RANK_PREVIEW]]
        if len(group["ranking"]) > RANK_PREVIEW:
            tile.controls.append(
                ft.TextButton(
                    f"显示本组全部 {len(group['ranking'])} 家",
                    on_click=show_all(group, tile),
                    height=48,
                )
            )
        tiles.append(tile)
    browse = ft.Column(tiles, spacing=0, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    matches = ft.Column(visible=False, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def search(e):
        if not is_current():
            return
        term = (e.control.value or "").strip().casefold()
        found = [
            row
            for group in groups
            for row in group["ranking"]
            if term and term in f"{group['industry']} {row['code']} {row['name']}".casefold()
        ]
        matches.controls = [row_control(r) for r in found[:SEARCH_LIMIT]] or [
            paragraph("没有匹配的公司或行业，请调整筛选。")
        ]
        if len(found) > SEARCH_LIMIT:
            matches.controls.append(
                paragraph(
                    f"共 {len(found)} 家匹配，仅显示前 {SEARCH_LIMIT} 家；请输入更具体的条件。"
                )
            )
        matches.visible, browse.visible = bool(term), not term
        e.page.update()

    return [ft.TextField(label="按代码、名称或行业筛选", on_change=search), matches, browse]


def result_view(
    run: dict[str, Any], open_route, back, code: str | None = None, is_current=lambda: True
) -> ft.Column:
    controls: list[ft.Control] = []
    source = run.get("request", {}).get("from")
    if source:
        controls.append(
            ft.TextButton("返回来源初筛", on_click=open_route("/analysis/" + source), height=48)
        )
    if code and run.get("result") and "board" in run["result"]:
        controls.append(
            opportunity_view(
                run["result"]["board"],
                code,
                lambda c: open_route("/analysis/" + run["id"] + "/" + c),
                open_route("/analysis/" + run["id"]),
            )
        )
        return ft.Column(controls, spacing=20)
    controls.append(
        ft.TextButton(icon=ft.Icons.ARROW_BACK, content="返回选股与研究", on_click=back, height=48)
    )
    status = run["status"]
    controls.append(
        notice(
            "执行：" + LABELS.get(status, "状态待核查"),
            run["message"],
            caution=status in {"failed", "interrupted"},
        )
    )
    evidence: list[ft.Control] = [
        paragraph(
            f"请求编号：{run['id']}\n发起时间（上海）：{local_time(run.get('created_at'))}\n本次类型：{run['limits']['title']}"
        )
    ]
    if status == "running":
        controls.extend(
            [
                heading("后台研究中", level=1),
                ft.ProgressRing(width=24, height=24),
                paragraph(
                    "覆盖：正在核查；结果：尚未生成。可稍后从最近研究打开；退出页面不取消已确认请求。"
                ),
            ]
        )
    else:
        result = run.get("result") if status in {"completed", "partial"} else None
        if not result:
            controls.extend(
                [
                    heading("没有可用研究结果", level=1),
                    paragraph(
                        "覆盖：未完成不可继续。下一步：核对失败原因；新一轮研究须再次明确确认成本。"
                    ),
                    paragraph(usage_text(run.get("usage"), run.get("request", {}).get("mode"))),
                ]
            )
        elif "board" in result:
            rows = result["board"]["rows"]
            controls.append(
                paragraph(
                    "覆盖："
                    + (
                        "已尽但有资料缺口；补齐后再研究。"
                        if any(r["status"] == "gap" for r in rows)
                        else "完整。"
                    )
                )
            )
            controls.append(
                opportunity_view(
                    result["board"],
                    None,
                    lambda c: open_route("/analysis/" + run["id"] + "/" + c),
                    back,
                )
            )
        elif run["request"]["mode"] == "financial" and result.get("reports"):
            controls.append(heading("财报研究", level=1))
            for report in result["reports"]:
                controls.extend(report_controls(report))
            evidence.append(paragraph(usage_text(result.get("usage"), run["request"]["mode"])))
        else:
            summary = result["coverage"]
            counts = summary["counts"]
            pending = counts.get("pending", 0)
            groups = result.get("groups", []) if run["request"]["mode"] == "screen" else []
            candidate_count = sum(len(g["ranking"]) for g in groups)
            reports = result.get("reports", [])
            controls.append(heading("财报筛选与研究", level=1))
            covered = (
                ("未完成可继续" if run.get("can_continue") else "未完成不可继续")
                if pending
                else "已尽但有资料缺口"
                if counts.get("gap")
                else "完整"
            )
            controls.append(
                paragraph(
                    f"覆盖：{covered}\n财报资格通过 {counts.get('qualified', 0)}；排除 {counts.get('excluded', 0)}；资料缺口 {counts.get('gap', 0)}；尚未覆盖 {pending}"
                )
            )
            controls.append(
                paragraph(
                    f"结果：{candidate_count} 家同业排名候选，{len(reports)} 份报告。"
                    if candidate_count or reports
                    else "结果：零候选/报告。尚未形成可比较候选；行业覆盖不足、资料缺口或资格未通过均可能导致零结果。"
                )
            )
            if run.get("continued_by"):
                controls.append(
                    ft.Button(
                        "已继续 → 打开最新进度",
                        on_click=open_route("/analysis/" + run["continued_by"]),
                        height=48,
                    )
                )
            elif pending:
                controls.append(
                    notice(
                        "初筛尚未完成",
                        "下一步：确认新增预算后继续未覆盖部分。规则变化或冻结资料超过七天时，须回入口确认新批次。",
                        caution=True,
                    )
                )
                if run.get("can_continue"):
                    controls.append(
                        ft.Button(
                            "继续未覆盖初筛（核对预算）",
                            on_click=open_route("/analysis?mode=screen&resume_from=" + run["id"]),
                            height=48,
                        )
                    )
            else:
                controls.append(
                    paragraph("下一步：选择公司研究财报或查看价格纪律参考；资料缺口不会自动重试。")
                )
            controls.append(paragraph(usage_text(result.get("usage"), run["request"]["mode"])))
            controls.append(
                paragraph(
                    "组内按 PB 与 ROE 平均名次排序，不是跨行业总榜。仅筛后池资料完整的行业形成候选；这是筛后同业排名，不是完整行业前三。财报资格不代表投资价值。"
                )
            )
            evidence.append(
                paragraph(
                    f"依据日期：{summary['data_date']}\n来源核查时间（上海）：\n{source_times_text(summary.get('source_times'))}\n证据截止：{summary.get('cutoff') or '未记录'}"
                )
            )
            screening = summary.get("prefilter")
            if screening:
                initial = summary.get("prefilter_counts", {})
                evidence.append(
                    paragraph(
                        f"冻结门槛：0＜PB≤{screening['pb_max']}；总市值≥{screening['total_mv_min_yi']}亿元，不设上限。\n财报采集前：排除 {initial.get('excluded', 0)} 家；资料缺口 {initial.get('gap', 0)} 家；进入财报采集池 {initial.get('pending', 0)} 家。\n门槛用于控制采集量，未经收益验证，可能漏选。"
                    )
                )
            if result.get("truncated"):
                controls.append(notice("排名展示已截断", result["truncation_note"], caution=True))
            if groups:
                controls.extend(ranking_controls(groups, run["id"], open_route, is_current))
    controls.append(ft.ExpansionTile(title=ft.Text("依据"), controls=evidence, controls_padding=16))
    return ft.Column(controls, spacing=20, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
