"""Window-index UI; actions and authorization remain owned by the app."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import flet as ft

from a_stock_tracker.opportunities import LIMITS, SECTIONS
from a_stock_tracker.research import SHANGHAI

BACKGROUND = "#E6EFF0"
SURFACE = "#F6FAFA"
BAND = "#DBE8EB"
INK = "#203536"
MUTED = "#486168"
ACCENT = "#006975"
LINE = "#BCCDCF"
CAUTION = "#805500"
CAUTION_BG = "#FFF1D6"
ERROR = "#99362B"
# Native radio/checkbox labels default to 14px; consent text must read at body size.
LABEL_STYLE = ft.TextStyle(size=16, color=INK)


def local_time(value: Any) -> str:
    if not value:
        return "未记录"
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            return str(value)
        return stamp.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(value)


def window_theme() -> ft.Theme:
    return ft.Theme(
        font_family="NotoSansSC",
        color_scheme_seed=ACCENT,
        color_scheme=ft.ColorScheme(
            primary=ACCENT,
            on_primary=SURFACE,
            primary_container=BAND,
            on_primary_container=INK,
            surface=SURFACE,
            on_surface=INK,
            on_surface_variant=MUTED,
            outline=LINE,
            error=ERROR,
        ),
        text_button_theme=ft.TextButtonTheme(
            style=ft.ButtonStyle(
                color=ACCENT,
                padding=ft.Padding.symmetric(horizontal=12, vertical=12),
                shape=ft.RoundedRectangleBorder(radius=6),
                # Stretched columns widen text links; keep them reading as left-edge navigation.
                alignment=ft.Alignment.CENTER_LEFT,
            )
        ),
        button_theme=ft.ButtonTheme(
            style=ft.ButtonStyle(
                color={ft.ControlState.DEFAULT: SURFACE, ft.ControlState.DISABLED: MUTED},
                bgcolor={ft.ControlState.DEFAULT: ACCENT, ft.ControlState.DISABLED: BAND},
                padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                shape=ft.RoundedRectangleBorder(radius=6),
                elevation=0,
            )
        ),
        divider_color=LINE,
    )


def heading(value: str, size: int = 22, level: int = 2) -> ft.Semantics:
    return ft.Semantics(
        heading_level=level,
        content=ft.Text(value, size=size, weight=ft.FontWeight.BOLD, color=INK),
    )


def paragraph(value: str) -> ft.Text:
    return ft.Text(value, size=16, color=INK, style=ft.TextStyle(height=1.55))


def notice(
    title: str,
    detail: str,
    *,
    caution: bool = False,
    level: int | None = None,
    extra: list[ft.Control] | None = None,
) -> ft.Container:
    heading_level = level if level is not None else (2 if caution else None)
    return ft.Container(
        content=ft.Column(
            [
                ft.Semantics(
                    heading_level=heading_level,
                    content=ft.Text(
                        title, size=16, weight=ft.FontWeight.BOLD, color=CAUTION if caution else INK
                    ),
                ),
                paragraph(detail),
                *(extra or []),
            ],
            spacing=8,
            tight=True,
        ),
        bgcolor=CAUTION_BG if caution else BAND,
        padding=16,
        border=ft.Border(left=ft.BorderSide(3, CAUTION if caution else ACCENT)),
    )


def opportunity_view(
    board: dict[str, Any] | None,
    code: str | None,
    open_company,
    go_back,
    action: ft.Control | None = None,
    *,
    embedded: bool = False,
) -> ft.Column:
    controls: list[ft.Control] = []
    if code:
        controls.append(
            ft.TextButton(
                icon=ft.Icons.ARROW_BACK, content="返回波段观察", on_click=go_back, height=48
            )
        )
    else:
        controls.extend(
            [
                heading("波段观察", size=22 if embedded else 30, level=2 if embedded else 1),
                paragraph("先读解释与反证，再核对价格条件。"),
            ]
        )
        if action:
            controls.append(action)
    if board is None:
        controls.extend(
            [
                ft.Divider(),
                heading("还没有经过核验的机会报告", level=1 if code else 2),
                paragraph(
                    "尚未配置或发布批次。打开页面不会请求行情或模型，也不会把旧财报排名当作波段推荐。"
                ),
            ]
        )
    else:
        source = "来源：" + board["source"]
        if board.get("created_at"):
            source += "\n批次生成：" + local_time(board["created_at"])
        controls.append(
            notice(
                "合成演示 · 非真实股票、非AI研究结果"
                if board.get("demo")
                else "显式股票池 · 非全市场精选",
                source if board.get("demo") else source + "\nAI复核不等于投资逻辑正确。",
            )
        )
        rows = board["rows"]
        if code:
            rows = [row for row in rows if row["code"] == code]
            if not rows:
                controls.append(heading("本批次没有该公司的观察记录。", level=1))
        available = [r for r in rows if r["status"] == "observation" and not r.get("expired")]
        if not code:
            controls.append(
                ft.Text(
                    f"{len(available)} 项有效观察 · {len(rows)} 家显式股票池",
                    size=14,
                    color=MUTED,
                )
            )
            if not available:
                controls.append(heading("本批次没有有效观察候选，不凑推荐。"))
        for row in rows:
            controls.extend(
                [
                    ft.Divider(height=24),
                    # Name and code form one identity; keep them tighter than section spacing.
                    ft.Column(
                        [
                            heading(row["name"], level=1 if code else 2),
                            ft.Text(row["code"], size=14, color=MUTED),
                        ],
                        spacing=4,
                        tight=True,
                    ),
                ]
            )
            if row["status"] != "observation":
                controls.append(
                    notice(
                        "未入选" if row["status"] == "excluded" else "资料或解读核验失败",
                        "\n".join(row["reasons"]),
                        caution=True,
                    )
                )
                if row["status"] == "excluded":
                    controls.append(
                        paragraph(
                            f"{row.get('facts', {}).get('distance', '实际距离：未记录')}\n规则上限：8%\n依据日期：{row.get('as_of') or '未记录'}\n下一步：本次不满足观察规则，可返回初筛核查其他公司或另行确认财报研究。"
                        )
                    )
                else:
                    controls.append(paragraph("下一步：核对并补齐资料后，再明确确认新的研究。"))
                continue
            controls.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(f"依据截至 {row['as_of']}", size=14, color=MUTED),
                            ft.Text(f"观察有效至 {row['expires']}", size=14, color=MUTED),
                        ],
                        spacing=8,
                        tight=True,
                    ),
                    bgcolor=BAND,
                    padding=16,
                )
            )
            if row.get("expired"):
                controls.append(
                    notice(
                        "观察已过期 · 仅供历史查看",
                        "价格条件已隐藏；历史解释与量价依据不代表当前仍有效。",
                        caution=True,
                    )
                )
            else:
                controls.append(ft.Text("等待条件核对 · 不是已触发信号", size=14, color=ACCENT))
            explanation = row["explanation"]
            section_level = 2 if code else 3
            for key, title in SECTIONS.items():
                if key == "counter":
                    controls.append(
                        notice(title, explanation[key]["text"], caution=True, level=section_level)
                    )
                else:
                    controls.extend(
                        [
                            heading(title, size=18, level=section_level),
                            paragraph(explanation[key]["text"]),
                        ]
                    )
            if code:
                facts = row["facts"]
                if not row.get("expired"):
                    controls.append(
                        ft.Container(
                            content=ft.Column(
                                [
                                    heading("程序价格条件", size=22),
                                    heading("什么条件才重新评估", size=18, level=3),
                                    paragraph(facts["condition"]),
                                    heading("什么时候放弃", size=18, level=3),
                                    paragraph(facts["invalidation"]),
                                    heading("价格距离，不是损失上限", size=18, level=3),
                                    paragraph(facts["distance"]),
                                ],
                                spacing=16,
                                tight=True,
                            ),
                            bgcolor=SURFACE,
                            padding=20,
                            border=ft.Border(left=ft.BorderSide(3, ACCENT)),
                        )
                    )
                controls.append(paragraph("首版仅作量价观察，不含公告催化、财报拐点或资金验证。"))
                fact_names = {"trend": "趋势", "relative": "相对强弱", "limits": "缺口与执行风险"}
                if not row.get("expired"):
                    fact_names.update(
                        {"condition": "触发条件", "invalidation": "失效条件", "distance": "距离"}
                    )
                evidence: list[ft.Control] = [
                    ft.Semantics(
                        label=f"{fact_names[k]}：{v}",
                        read_only=True,
                        exclude_semantics=True,
                        content=ft.Text(
                            f"{fact_names[k]}：{v}",
                            selectable=True,
                            size=16,
                            color=INK,
                            style=ft.TextStyle(height=1.55),
                        ),
                    )
                    for k, v in facts.items()
                    if k in fact_names and not (k == "limits" and v == LIMITS)
                ]
                evidence.extend(
                    ft.Text(
                        f"{SECTIONS[key]}依据：{'、'.join(section['refs'])}", size=14, color=MUTED
                    )
                    for key, section in row["explanation"].items()
                )
                if row.get("model"):
                    evidence.append(
                        ft.Text(
                            f"解释与复核模型：{row['model']['provider']} / {row['model']['model']}",
                            size=14,
                            color=MUTED,
                        )
                    )
                controls.append(
                    ft.ExpansionTile(
                        title=ft.Text("核查依据与AI引用", size=16, color=ACCENT),
                        subtitle=ft.Text("均线、区间、量能、相对强弱与缺口", size=14, color=MUTED),
                        controls=evidence,
                        controls_padding=16,
                        tile_padding=ft.Padding.symmetric(horizontal=16, vertical=8),
                        bgcolor=SURFACE,
                        collapsed_bgcolor=SURFACE,
                        text_color=ACCENT,
                        collapsed_text_color=ACCENT,
                        icon_color=ACCENT,
                        collapsed_icon_color=ACCENT,
                    )
                )
            else:
                controls.append(
                    ft.Button(
                        ft.Text(
                            "查看条件与反证",
                            semantics_label="查看条件与反证 · " + row["name"],
                            size=16,
                        ),
                        on_click=open_company(row["code"]),
                        height=48,
                    )
                )
    controls.extend([ft.Divider(height=24), paragraph(LIMITS)])
    return ft.Column(controls, spacing=16, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
