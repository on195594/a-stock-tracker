"""Read-only opportunity pages; all actions remain owned by the authenticated app."""

from __future__ import annotations

from typing import Any

import flet as ft

from a_stock_tracker.opportunities import LIMITS, SECTIONS


def opportunity_view(
    board: dict[str, Any] | None, code: str | None, open_company, go_back, open_legacy
) -> ft.Column:
    controls: list[ft.Control] = []
    if code:
        controls.append(ft.TextButton("返回波段机会", on_click=go_back))
    else:
        controls.extend(
            [
                ft.Text("波段机会", size=28, weight=ft.FontWeight.BOLD),
                ft.Text("数周至数月 · 收盘后观察 · 候选与价格条件", size=14),
                ft.Text("先看为什么值得等，再看什么条件才值得重新评估。", size=16),
            ]
        )
    if board is None:
        controls.extend(
            [
                ft.Text("还没有经过核验的机会报告", size=20, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "尚未配置或发布批次。打开页面不会请求行情或模型，也不会把旧财报排名当作波段推荐。"
                ),
            ]
        )
    else:
        controls.append(
            ft.Text(
                "合成演示 · 非真实股票、非AI研究结果"
                if board.get("demo")
                else "显式股票池 · 非全市场精选；AI复核不等于投资逻辑正确",
                size=13,
            )
        )
        controls.append(ft.Text("来源：" + board["source"], size=12))
        if board.get("created_at"):
            controls.append(ft.Text("批次生成：" + board["created_at"], size=12))
        rows = board["rows"]
        if code:
            rows = [row for row in rows if row["code"] == code]
            if not rows:
                controls.append(ft.Text("本批次没有该公司的观察记录。"))
        available = [r for r in rows if r["status"] == "observation" and not r.get("expired")]
        if not code and not available:
            controls.append(
                ft.Text("本批次没有有效观察候选，不凑推荐。", size=20, weight=ft.FontWeight.BOLD)
            )
        for row in rows:
            controls.append(ft.Divider())
            controls.append(
                ft.Text(f"{row['name']}  {row['code']}", size=22, weight=ft.FontWeight.BOLD)
            )
            if row["status"] != "observation":
                controls.append(
                    ft.Text("未入选" if row["status"] == "excluded" else "资料或解读核验失败")
                )
                controls.extend(ft.Text(reason) for reason in row["reasons"])
                continue
            controls.append(
                ft.Text(f"依据截至 {row['as_of']} · 观察有效至 {row['expires']}", size=12)
            )
            if row.get("expired"):
                controls.append(
                    ft.Text(
                        "观察已过期；以下仅为历史解释，不展示可行动的价格条件。",
                        weight=ft.FontWeight.BOLD,
                    )
                )
            else:
                controls.append(ft.Text("等待条件核对 · 不是已触发信号", weight=ft.FontWeight.BOLD))
            controls.append(ft.Text(row["explanation"]["reason"]["text"], size=16))
            if code:
                if not row.get("expired"):
                    for key, label in (
                        ("condition", "什么条件才重新评估"),
                        ("invalidation", "什么时候放弃"),
                        ("distance", "价格距离，不是损失上限"),
                    ):
                        controls.extend(
                            [
                                ft.Text(label, size=18, weight=ft.FontWeight.BOLD),
                                ft.Text(row["facts"][key], size=16),
                            ]
                        )
                for key in ("counter", "wait"):
                    controls.extend(
                        [
                            ft.Text(SECTIONS[key], size=18, weight=ft.FontWeight.BOLD),
                            ft.Text(row["explanation"][key]["text"], size=16),
                        ]
                    )
                hidden = {"condition", "invalidation", "distance"} if row.get("expired") else set()
                evidence: list[ft.Control] = [
                    ft.Text(value, selectable=True)
                    for key, value in row["facts"].items()
                    if key not in hidden
                ]
                evidence.extend(
                    ft.Text(f"{SECTIONS[key]}依据：{'、'.join(section['refs'])}", size=12)
                    for key, section in row["explanation"].items()
                )
                if row.get("model"):
                    evidence.append(
                        ft.Text(
                            f"解释与复核模型：{row['model']['provider']} / {row['model']['model']}",
                            size=12,
                        )
                    )
                controls.append(
                    ft.ExpansionTile(title=ft.Text("核查依据与AI引用"), controls=evidence)
                )
            else:
                if not row.get("expired"):
                    controls.append(ft.Text(row["facts"]["condition"]))
                controls.append(ft.Text("反对理由：" + row["explanation"]["counter"]["text"]))
                controls.append(
                    ft.Button("查看条件与反证 · " + row["name"], on_click=open_company(row["code"]))
                )
    controls.extend(
        [
            ft.Divider(),
            ft.Text(LIMITS, size=12),
            ft.TextButton("历史研究工作台（兼容入口）", on_click=open_legacy),
        ]
    )
    return ft.Column(controls, spacing=16)
