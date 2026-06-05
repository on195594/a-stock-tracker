from __future__ import annotations

from typing import Any

import akshare as ak


def stock_individual_info_em(code: str, timeout: int) -> Any:
    return ak.stock_individual_info_em(symbol=code, timeout=timeout)


def stock_financial_abstract_ths(code: str) -> Any:
    return ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")


def stock_history_dividend_detail(code: str) -> Any:
    return ak.stock_history_dividend_detail(symbol=code, indicator="分红")


def stock_zh_a_hist(code: str, start_date: str, end_date: str) -> Any:
    return ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="",
    )


def stock_zh_a_spot_em() -> Any:
    return ak.stock_zh_a_spot_em()


def stock_zh_valuation_baidu(code: str) -> Any:
    return ak.stock_zh_valuation_baidu(
        code,
        indicator="市净率",
        period="近十年",
    )


def stock_financial_report_sina(code: str) -> Any:
    prefix = "sh" if code.startswith("6") else "sz"
    return ak.stock_financial_report_sina(stock=f"{prefix}{code}", symbol="利润表")
