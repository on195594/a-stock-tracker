"""Selected-evidence numeric validation: units, dates, percentages and snapshot isolation."""

from a_stock_tracker import disclosures as d


def number_case():
    packet = {"structured": {}, "documents": [{"id": "123", "pages": [{"page": 1, "text": ""}]}]}
    report = {"facts": [], "analysis": {}}
    quote = "截至2026年6月30日，2026年上半年营业收入（元） 16,703,694,265.38 15,181,329,651.13 10.03% 经营活动产生的现金流量净额 -1,107,268,584.24"
    packet["documents"][0]["pages"].append({"page": 2, "text": quote})
    packet["documents"][0].update(period="2026-06-30", announced="2026-08-15")
    packet["structured"]["market_snapshot"] = {
        "date": "2026-10-16",
        "close": 33.72,
        "pe_ttm": 25.41,
    }
    report["facts"].append(
        {
            "id": "F5",
            "document": "123",
            "page": 2,
            "quote": quote,
            "claim": "营业收入同比增长10.03%",
        }
    )
    return packet, report


def test_analysis_numbers_allow_unit_conversion_rounding_sign_and_snapshot():
    packet, report = number_case()
    report["analysis"]["financial"] = {
        "text": "截至6月30日，2026年上半年营收167.04亿元（约167亿），同比增长10.03%（约10%）；经营现金流净流出11.07亿元。分两个板块，见F5。",
        "facts": ["F5", "F1"],
    }
    report["analysis"]["valuation"] = {"text": "收盘33.72元，市盈率约25.4倍。", "facts": ["MKT"]}
    assert d.analysis_number_errors(report, packet) == {}


def test_evidence_packet_carries_market_snapshot_in_yi():
    row = {
        "code": "603606.SH",
        "name": "合成",
        "industry": "电气设备",
        "pb": 3.1,
        "valuation_date": "2026-10-16",
        "annual_roes": [],
        "roe_mean": 12.0,
        "research_report": {},
        "close": 33.72,
        "pe_ttm": 25.41,
        "dv_ttm": 1.2,
        "total_mv": 2318960.5,
    }
    doc = {
        "id": "1",
        "sha256": "a" * 64,
        "period": "2026-06-30",
        "pages": [{"page": 1, "text": "x" * 20}],
    }
    snapshot = d.evidence_packet([doc], row)["structured"]["market_snapshot"]
    assert snapshot == {
        "date": "2026-10-16",
        "close": 33.72,
        "pe_ttm": 25.41,
        "pb": 3.1,
        "total_mv_yi": 231.9,
        "dv_ttm": 1.2,
    }


def test_number_guard_keeps_percent_and_amount_apart_and_accepts_cited_prior_years():
    packet, report = number_case()
    quote = "2025年毛利率15% 第1.2.3节 2025.12.31 营业收入（元） 3,750,000,000"
    packet["documents"][0]["pages"][0]["text"] += "\n" + quote
    report["facts"].append(
        {"id": "F6", "document": "123", "page": 1, "quote": quote, "claim": "毛利率15%"}
    )
    report["analysis"]["financial"] = {
        "text": "2025年毛利率15%，营收37.5亿元；见第1.2.3节，截至2025.12.31。",
        "facts": ["F6"],
    }
    assert d.analysis_number_errors(report, packet) == {}
    # A percentage cannot be restated as an amount, nor a scaled amount as a percentage.
    report["analysis"]["financial"]["text"] = "净利润15亿元，订单下滑37.5%。"
    assert d.analysis_number_errors(report, packet) == {"financial": ["15", "37.5%"]}


def test_percent_column_and_page_level_number_sources():
    # Production 603606.SH: a one-row quote under a "(%)" header, and section figures that sit
    # on the cited page next to a narrower quote.
    page = "本报告期比上年同期增减(%)\n营业收入 5,799,552,532.02 4,432,278,764.22 30.85\n归属于上市公司股东的净利润 564,785,958.32 472,962,984.86 19.41"
    fact = {
        "id": "F1",
        "document": "1",
        "page": 1,
        "quote": "营业收入 5,799,552,532.02 4,432,278,764.22 30.85",
        "claim": "营业收入同比增减为30.85%",
    }
    assert d.unquoted_numbers(fact | {"quote": page}) == []
    assert d.unquoted_numbers(fact) == ["30.85%"]
    # Production 601088.SH: the percent column header is a bare "%" cell.
    bare = {
        "quote": "项目   本期   上期   变动\n                      %\n营业收入 189,338 175,423 7.9"
    }
    assert d.unquoted_numbers(fact | bare | {"claim": "营业收入变动7.9%"}) == []
    assert d.unquoted_numbers(fact | bare | {"claim": "营业收入变动7.8%"}) == ["7.8%"]
    assert d.unquoted_numbers({"quote": "增长率为7.9", "claim": "增长7.9%"}) == ["7.9%"]
    # Production 600900/600519: "下降2.35%" for a "-2.35" cell; only a decline word drops the sign.
    fell = {"quote": page.replace(" 30.85", " -30.85")}
    assert d.unquoted_numbers(fact | fell | {"claim": "营业收入同比下降30.85%"}) == []
    assert d.unquoted_numbers(fact | fell | {"claim": "营业收入同比增长30.85%"}) == ["30.85%"]
    assert d.unquoted_numbers(fact | fell | {"claim": "营业收入同比下降了30.85%"}) == []
    # Codex review: a decline word that is negated or turned around carries no sign.
    for claim in ("营业收入同比由下降转增30.85%", "营业收入同比未下降30.85%"):
        assert d.unquoted_numbers(fact | fell | {"claim": claim}) == ["30.85%"]
    assert d.unquoted_numbers(fact | {"claim": "营业收入同比增减为-30.85%", "quote": page}) == [
        "-30.85%"
    ]
    packet = {
        "structured": {"code": "603606.SH"},
        "documents": [
            {
                "id": "1",
                "period": "2026-06-30",
                "announced": "2026-08-05",
                "pages": [{"page": 1, "text": page}],
            }
        ],
    }
    report = {
        "facts": [fact],
        "analysis": {
            "financial": {
                "text": "收入57.9955亿元，增长30.85%；归母净利润5.6479亿元，增长19.41%。",
                "facts": ["F1"],
            }
        },
    }
    assert d.analysis_number_errors(report, packet) == {"financial": ["5.6479", "19.41%"]}
    report["facts"][0]["quote"] = page
    assert d.analysis_number_errors(report, packet) == {}
    report["analysis"]["financial"]["text"] = "归母净利润6.1亿元。"
    assert d.analysis_number_errors(report, packet) == {"financial": ["6.1"]}


def test_number_guard_rejects_unit_crossing_small_amounts_and_invented_dates():
    # Codex review 2026-10-19: each case below used to pass validate_report.
    page = "营业收入（元） 4,275,000,000 同比增长10.03% 截至 2026 年 8 月 3 日 在手订单 179.34 亿元"
    packet = {
        "structured": {"code": "x", "market_snapshot": {"close": 33.72, "date": "2026-10-16"}},
        "documents": [
            {
                "id": "1",
                "period": "2026-06-30",
                "announced": "2026-08-15",
                "pages": [{"page": 1, "text": page}],
            }
        ],
    }
    fact = {"id": "F1", "document": "1", "page": 1, "quote": page, "claim": "c"}

    def errors(text, facts=("F1",)):
        report = {"facts": [fact], "analysis": {"s": {"text": text, "facts": list(facts)}}}
        return d.analysis_number_errors(report, packet).get("s", [])

    assert errors("营收33.72亿元。") == ["33.72"]  # snapshot counts only with MKT
    assert errors("收盘33.72元，快照截至2026年10月16日。", ("MKT",)) == []
    assert errors("净利润12亿元。") == ["12"]
    assert errors("分3个板块，6月30日止。") == ["6月30日"]
    assert errors("2026年12月31日有新订单。") == ["2026年12月31日"]
    assert errors("截至2026年8月3日在手订单179.34亿元，营收42.75亿元，增长10.03%。") == []
    # A stray "%" elsewhere in the quote no longer licenses "%" on another number.
    claim = {"quote": "营业收入（元）42.75，同比增长10.03%", "claim": "收入同比增长42.75%"}
    assert d.unquoted_numbers(claim) == ["42.75%"]
