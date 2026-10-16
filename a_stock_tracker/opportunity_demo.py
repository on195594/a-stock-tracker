"""Synthetic opportunity board, used only by loopback demo and offline tests."""

from datetime import timedelta

from a_stock_tracker.opportunities import REFERENCE_RULE, evaluate, today


def demo_packet():
    day = today() - timedelta(days=1)
    history = []
    while len(history) < 61:
        if day.weekday() < 5:
            history.append(day.isoformat())
        day -= timedelta(days=1)
    history.reverse()
    future = []
    day = today()
    while len(future) < 5:
        if day.weekday() < 5:
            future.append(day.isoformat())
        day += timedelta(days=1)

    def prices(step):
        return [
            {
                "date": day,
                "open": round(10 + i * step, 2),
                "close": round(10 + i * step, 2),
                "high": round(10.02 + i * step, 2),
                "low": round(9.98 + i * step, 2),
                "pre_close": round(10 + (i - 1) * step, 2),
                "vol": 10000.0,
            }
            for i, day in enumerate(history)
        ]

    return {
        "code": "600001.SH",
        "name": "演示公司",
        "sessions": history,
        "future_sessions": future,
        "bars": prices(0.02),
        "benchmark": prices(0.005),
    }


def demo_board(scenario="reference"):
    packet = demo_packet()
    if scenario == "wide":
        packet["bars"][-5]["low"] = 8.0
    row = evaluate(packet)
    row["expired"] = scenario == "expired"
    if scenario == "gap":
        row = {
            "code": row["code"],
            "name": row["name"],
            "status": "gap",
            "reasons": ["合成日线资料缺失；补齐资料后另行确认研究。"],
        }
    return {
        "rule": REFERENCE_RULE,
        "demo": True,
        "source": "本地合成日线，未调用行情或模型",
        "rows": [] if scenario == "zero" else [row],
    }


def demo_report(code):
    return {
        "code": code,
        "name": "演示公司",
        "status": "synthetic",
        "evidence_review": "not_real",
        "quality": {
            "generated": 2,
            "repair": "repaired",
            "dropped": [{"id": "F2", "reason": "citation_mismatch"}],
            "unformed": {},
            "passed": 1,
        },
        "documents": [{"period": "2025-12-31", "announced": "2026-04-01"}],
        "clues": {
            "industry": "合成行业",
            "valuation_date": "2026-04-01",
            "pb": 1.0,
            "roe_mean": 10.0,
        },
        "analysis": {
            "business": {
                "text": "合成财报报告 · 非真实研究结果。示例业务依赖单一产品。",
                "facts": ["F1"],
            },
            "financial": {"text": "合成推论：仍需核对回款质量。", "facts": ["F1"]},
            "valuation": {"text": "合成假设：PB 需要结合资产质量理解。", "facts": ["F1"]},
            "counterevidence": {
                "text": "合成反证：单一产品收入集中。需求变化可能影响回款，尚缺订单证据。",
                "facts": ["F1"],
            },
            "change_conditions": {
                "text": "合成条件：补齐订单与回款原文后重新判断。",
                "facts": ["F1"],
            },
        },
        "facts": [
            {
                "id": "F1",
                "claim": "合成收入集中事实",
                "title": "合成年报",
                "page": 12,
                "period": "2025-12-31",
                "announced": "2026-04-01",
                "quote": "合成引文：收入主要来自单一产品。",
                "url": "https://example.invalid/synthetic.pdf",
            }
        ],
        "gaps": ["合成缺口：未覆盖临时公告", "合成缺口：订单资料缺失"],
        "omitted_pages": 2,
        "error": None,
    }
