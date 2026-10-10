"""Synthetic opportunity board, used only by loopback demo and offline tests."""

from datetime import timedelta

from a_stock_tracker.opportunities import evaluate, today


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


def demo_board():
    row = evaluate(demo_packet())
    row.update(
        status="observation",
        expired=False,
        explanation={
            "reason": {
                "text": "这是合成示例：价格趋势向上，且近期相对基准更强，因此值得等待后续确认，而不是立即追涨。",
                "refs": ["trend", "relative"],
            },
            "counter": {
                "text": "相对强势也可能是假突破；这里只核对了量价，没有公告和板块催化支持，无法确认上涨能否延续。",
                "refs": ["limits", "relative"],
            },
            "wait": {
                "text": "等待后续完整日线同时满足价格与成交量条件，再核查最新公告；越过区间不追，跌破失效位置放弃。",
                "refs": ["condition", "invalidation"],
            },
        },
    )
    return {"demo": True, "source": "本地合成日线，未调用行情或模型", "rows": [row]}
