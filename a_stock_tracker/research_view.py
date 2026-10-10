"""Read research coverage and reports without inventing candidates or hidden costs."""

from __future__ import annotations

from typing import Any

import flet as ft

from a_stock_tracker.opportunity_view import heading, notice, opportunity_view, paragraph

LABELS = {
    "running": "研究执行中",
    "completed": "研究结果",
    "partial": "研究结束 · 部分覆盖或有缺口",
    "failed": "研究失败",
    "interrupted": "研究中断",
}


def result_view(run: dict[str, Any], open_route, back, code: str | None = None) -> ft.Column:
    if code and run.get("result") and "board" in run["result"]:
        return opportunity_view(
            run["result"]["board"],
            code,
            lambda c: open_route("/analysis/" + run["id"] + "/" + c),
            open_route("/analysis/" + run["id"]),
        )
    controls: list[ft.Control] = [ft.TextButton("返回选股与研究", on_click=back, height=48)]
    status = run["status"]
    controls.append(
        notice(
            LABELS.get(status, status),
            run["message"],
            caution=status in {"partial", "failed", "interrupted"},
        )
    )
    controls.append(
        paragraph(
            f"请求编号：{run['id']}\n发起时间：{run['created_at']}\n本次类型：{run['limits']['title']}"
        )
    )
    if run.get("demo"):
        controls.append(notice("合成演示 · 非真实研究结果", "未请求行情、公告或模型，不产生费用。"))
    if status == "running":
        controls.extend(
            [
                heading("后台研究中", level=1),
                ft.ProgressRing(width=24, height=24),
                paragraph(
                    "可留在这里等待，也可稍后从研究记录打开。退出页面不取消已确认请求；不重复点击启动。"
                ),
            ]
        )
        return ft.Column(controls, spacing=20, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    result = run.get("result") if status in {"completed", "partial"} else None
    if not result:
        controls.extend(
            [
                heading("没有可用研究结果", level=1),
                paragraph("失败或中断不等于旧资料通过本次复核。新一轮研究须再次明确确认成本。"),
            ]
        )
    elif "board" in result:
        controls.append(
            opportunity_view(
                result["board"],
                code,
                lambda c: open_route("/analysis/" + run["id"] + "/" + c),
                open_route("/analysis/" + run["id"]) if code else back,
            )
        )
    else:
        summary = result["coverage"]
        count_names = {
            "qualified": "财报资格通过",
            "excluded": "排除",
            "gap": "资料缺口",
            "pending": "尚未覆盖",
        }
        counts = "；".join(
            f"{name} {summary['counts'].get(key, 0)}" for key, name in count_names.items()
        )
        scope = "指定样本" if summary["mode"] == "sample" else "全市场尝试"
        covered = "部分覆盖或有缺口" if summary["status"] == "partial" else "覆盖完成"
        controls.extend(
            [
                heading("财报筛选与研究", level=1),
                notice(
                    "财报资格不是波段推荐",
                    f"依据日期：{summary['data_date']}\n范围：{scope} · {covered}\n总记录：{summary['total']}；{counts}\n仅完整行业形成可比较候选；样本只验证指定公司。未覆盖与失败不由模型补齐。\n来源核查时间：{summary.get('source_times') or '未记录'}；证据截止：{summary.get('cutoff') or '未记录'}",
                ),
            ]
        )
        if not result["candidates"]:
            controls.append(
                paragraph(
                    "本次没有可核查候选，不凑推荐。可回入口研究指定股票，或另行确认一次新的筛选。"
                )
            )
        for row in result["candidates"]:
            c = row["code"]
            controls.extend(
                [
                    ft.Divider(),
                    heading(f"{row['name']} · {c}"),
                    paragraph(
                        f"行业：{row['industry']}\nPB（倍）：{row['pb']}；历史年度平均 ROE：{row['roe_mean']}%\n仅财报初筛资格，不是投资价值验证。\n核查时间：{row.get('checked_at') or '未记录'}"
                    ),
                    ft.Button(
                        "核对量价条件",
                        on_click=open_route("/analysis?mode=swing&code=" + c),
                        height=48,
                    ),
                    ft.TextButton(
                        "研究这家公司财报",
                        on_click=open_route("/analysis?mode=financial&code=" + c),
                        height=48,
                    ),
                ]
            )
        for report in result["reports"]:
            controls.extend(
                [
                    heading("报告 · " + report["code"]),
                    notice(
                        "核查状态",
                        f"报告状态：{report['status']}\n证据复核：{report['evidence_review']}",
                    ),
                    # Plain text prevents model-generated links/images from making external requests.
                    paragraph(report["text"]),
                ]
            )
    return ft.Column(controls, spacing=20, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
