"""Offline tests for the approved 603606 official-document pilot."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import qualitative_v2_orient_cable_pilot as pilot
from qualitative_v2_orient_cable_pilot import OfficialFetchResult, collect_orient_cable_context, create_pilot_run_root
from qualitative_v2_production_contexts import ContextCollectionError, ProductionContextAuthorization
from qualitative_v2_validator import validate_context_dict

ALLOWED_HOSTS = (
    "static.cninfo.com.cn",
    "static.sse.com.cn",
    "www.cninfo.com.cn",
    "www.orientcable.com",
    "www.sse.com.cn",
)


def _authorization(http_limit: int = 12) -> ProductionContextAuthorization:
    return ProductionContextAuthorization(
        "qualitative-v2-orient-cable-pilot-test-01",
        "orient-cable",
        http_limit,
        target_codes=("603606",),
        pdf_download_limit=3,
        gemini_logical_call_limit=1,
        gemini_http_attempt_limit=3,
        allowed_hosts=ALLOWED_HOSTS,
        as_of_date="2026-07-19",
    )


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE stock_fundamentals (code TEXT PRIMARY KEY, data TEXT)")
    connection.execute(
        "INSERT INTO stock_fundamentals VALUES (?, ?)",
        ("603606", json.dumps({"roe_3y_avg": 15.5, "report_period": "2025"})),
    )
    return connection


def _fetcher(*, with_sentiment: bool = False):
    def fetch(url: str, _allowed_hosts: tuple[str, ...]) -> OfficialFetchResult:
        if url == pilot.ANNOUNCEMENT_PAGE:
            body = b"<script>GetPages(1)</script>"
            return OfficialFetchResult(url, url, 200, "text/html", body)
        if url == pilot.ANNOUNCEMENT_LIST:
            body = '<a href="javascript:download(42)">宁波东方电缆股份有限公司2025年年度报告</a>'.encode()
            return OfficialFetchResult(url, url, 200, "text/html", body)
        if "ajax_down_view" in url:
            body = json.dumps(
                {"err": "", "pUrl": "https://static.cninfo.com.cn/finalpage/2026-03-28/report.PDF"}
            ).encode()
            return OfficialFetchResult(url, url, 200, "application/json", body)
        if url.endswith("report.PDF") or url == pilot.ANNUAL_REPORT_URL:
            return OfficialFetchResult(url, url, 200, "application/pdf", b"pdf-fixture")
        if url == pilot.ABOUT_PAGE:
            body = "2025年度研发投入达3.8亿元 核心市场覆盖率超80% 全球海缆最具竞争力企业10强".encode()
            return OfficialFetchResult(url, url, 200, "text/html", body)
        if url == pilot.NEWS_PAGE:
            return OfficialFetchResult(url, url, 200, "text/html", "最新动态 2026.04.28".encode())
        announcements: list[dict[str, object]] = []
        if with_sentiment and "%E4%B8%AD%E6%A0%87" in url:
            announcements.append(
                {
                    "secCode": "603606",
                    "secName": "东方电缆",
                    "announcementId": "fixture-contract",
                    "announcementTitle": "重大项目中标公告",
                    "announcementContent": "项目合同履行期限为3年，将持续交付。",
                    "announcementTime": "2026-07-10",
                }
            )
        return OfficialFetchResult(
            url,
            url,
            200,
            "application/json",
            json.dumps({"announcements": announcements}, ensure_ascii=False).encode(),
        )

    return fetch


def test_collects_valid_context_and_stops_before_model_without_sentiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pilot,
        "_pdf_pages",
        lambda _raw: [
            "公司是国内陆缆系统、海缆系统核心供应商，荣膺最具竞争力企业 10 强。"
            "公司拥有 500kV 交流海缆及±535kV直流海缆研发生产能力。"
        ],
    )
    root = create_pilot_run_root(tmp_path, "pilot-no-sentiment")
    result = collect_orient_cable_context(
        _db(),
        authorization=_authorization(),
        run_root=root,
        as_of_date=date(2026, 7, 19),
        fetcher=_fetcher(),
    )
    assert result["status"] == "READY_FOR_HYBRID_MODEL"
    assert result["missing_score_dimensions"] == ["sentiment"]
    assert result["scoreable_dimensions"] == ["moat", "market_pos"]
    assert result["hybrid_ready"] is True
    assert result["http_attempts"] == 6
    assert result["pdf_downloads"] == 1
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["gemini_calls"] == 0
    assert manifest["hybrid_ready"] is True
    context = json.loads((root / "contexts" / "603606.json").read_text(encoding="utf-8"))
    assert validate_context_dict(context).valid


def test_recent_persistent_cninfo_evidence_makes_context_score_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pilot,
        "_pdf_pages",
        lambda _raw: [
            "公司是国内陆缆系统、海缆系统核心供应商，荣膺最具竞争力企业 10 强。"
            "公司拥有 500kV 交流海缆及±535kV直流海缆研发生产能力。"
        ],
    )
    root = create_pilot_run_root(tmp_path, "pilot-ready")
    result = collect_orient_cable_context(
        _db(),
        authorization=_authorization(),
        run_root=root,
        as_of_date=date(2026, 7, 19),
        fetcher=_fetcher(with_sentiment=True),
    )
    assert result["status"] == "READY_FOR_MODEL"
    assert result["missing_score_dimensions"] == []
    assert result["hybrid_ready"] is True


def test_generic_industry_language_is_not_company_direct_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pilot,
        "_pdf_pages",
        lambda _raw: ["行业研发投入持续提升，要求攻克关键核心技术。海外龙头市场份额较高，中国企业竞争力不断增强。"],
    )
    root = create_pilot_run_root(tmp_path, "pilot-generic-industry")
    result = collect_orient_cable_context(
        _db(),
        authorization=_authorization(),
        run_root=root,
        as_of_date=date(2026, 7, 19),
        fetcher=_fetcher(),
    )
    assert result["missing_score_dimensions"] == ["moat", "market_pos", "sentiment"]
    assert result["hybrid_ready"] is False


def test_cninfo_epoch_milliseconds_are_accepted_for_fresh_sentiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pilot,
        "_pdf_pages",
        lambda _raw: [
            "公司是国内陆缆系统、海缆系统核心供应商，荣膺最具竞争力企业 10 强。"
            "公司拥有 500kV 交流海缆及±535kV直流海缆研发生产能力。"
        ],
    )
    fetcher = _fetcher(with_sentiment=True)

    def timestamp_fetch(url: str, allowed_hosts: tuple[str, ...]) -> OfficialFetchResult:
        result = fetcher(url, allowed_hosts)
        if result.content_type == "application/json" and b"fixture-contract" in result.body:
            payload = json.loads(result.body)
            payload["announcements"][0]["announcementTime"] = 1783612800000
            return OfficialFetchResult(
                result.requested_url,
                result.final_url,
                result.status_code,
                result.content_type,
                json.dumps(payload, ensure_ascii=False).encode(),
            )
        return result

    root = create_pilot_run_root(tmp_path, "pilot-epoch")
    result = collect_orient_cable_context(
        _db(),
        authorization=_authorization(),
        run_root=root,
        as_of_date=date(2026, 7, 19),
        fetcher=timestamp_fetch,
    )
    assert result["status"] == "READY_FOR_MODEL"


def test_http_budget_stops_before_extra_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pilot,
        "_pdf_pages",
        lambda _raw: ["公司是国内陆缆系统、海缆系统核心供应商。公司拥有 500kV 交流海缆及±535kV直流海缆研发生产能力。"],
    )
    root = create_pilot_run_root(tmp_path, "pilot-budget")
    with pytest.raises(ContextCollectionError, match="budget exhausted"):
        collect_orient_cable_context(
            _db(),
            authorization=_authorization(http_limit=5),
            run_root=root,
            as_of_date=date(2026, 7, 19),
            fetcher=_fetcher(),
        )
