"""Research API client; no market/model credentials or execution in Web."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from a_stock_tracker.opportunity_demo import demo_board
from a_stock_tracker.research_service import LIMITS, quote, validate_request


class ResearchUnavailable(Exception):
    pass


class ResearchClient:
    def __init__(self, url: str, token: str, socket: str | None = None):
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
            or len(token) < 32
        ):
            raise ValueError("Invalid research service configuration")
        self.url, self.token, self.socket = url.rstrip("/"), token, socket

    def call(self, method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
        try:
            transport = httpx.HTTPTransport(uds=self.socket) if self.socket else None
            with httpx.Client(timeout=10, trust_env=False, transport=transport) as client:
                response = client.request(
                    method,
                    self.url + path,
                    headers={"Authorization": "Bearer " + self.token},
                    json=payload,
                )
                if len(response.content) > 4_000_000:
                    raise ValueError("Oversized research response")
                if response.status_code in {400, 404, 409, 503}:
                    try:
                        data = response.json()
                        detail = data.get("detail")
                        if isinstance(detail, str) and len(detail) <= 100:
                            raise ResearchUnavailable(detail)
                    except (ValueError, AttributeError):
                        pass
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict):
                    raise ValueError("Invalid research response")
                return value
        except (httpx.HTTPError, ValueError) as exc:
            raise ResearchUnavailable(
                "研究服务或受理状态暂不可确认。核对原请求，不自动重新收费。"
            ) from exc

    def info(self) -> dict[str, Any]:
        return self.call("GET", "/info")

    def recent(self) -> list[dict[str, Any]]:
        return self.call("GET", "/runs")["runs"]

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        validate_request(payload)
        return self.call("POST", "/runs", payload)

    def status(self, identifier: str) -> dict[str, Any]:
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ResearchUnavailable("请求编号不正确")
        return self.call("GET", "/runs/" + identifier)


class DemoResearchClient:
    """Synthetic runs survive page navigation only; never invoke real interfaces."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}

    def info(self) -> dict[str, Any]:
        return {
            "demo": True,
            "modes": {k: {**v, "available": True} for k, v in LIMITS.items()},
            "model": {"provider": "synthetic", "model": "no-real-calls"},
        }

    def recent(self) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in dict(r, status="completed").items() if k != "result"}
            for r in list(self.runs.values())[-10:][::-1]
        ]

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        validate_request(payload)
        if payload["id"] in self.runs:
            if self.runs[payload["id"]]["request"] != payload:
                raise ResearchUnavailable("原请求不能更改范围")
            return self.status(payload["id"])
        if payload["quote"] != quote(self.info(), payload["mode"]):
            raise ValueError("预算或模型已变化，请重新确认")
        if payload["mode"] == "swing":
            board = demo_board()
            board["rows"][0]["code"] = payload["codes"][0]
            result = {"board": board}
        else:
            result = {
                "coverage": {
                    "mode": "sample",
                    "data_date": "合成日期",
                    "status": "partial",
                    "total": 1,
                    "counts": {"qualified": 1},
                },
                "candidates": [
                    {
                        "code": (payload["codes"] or ["600001.SH"])[0],
                        "name": "演示公司",
                        "industry": "合成行业",
                        "pb": 1.0,
                        "roe_mean": 10.0,
                    }
                ],
                "reports": []
                if payload["mode"] == "screen"
                else [
                    {
                        "code": payload["codes"][0],
                        "status": "synthetic",
                        "evidence_review": "not_real",
                        "text": "合成财报报告 · 非真实研究结果。这里演示业务、财务、估值、最强反证与证据核查阅读；未请求行情、PDF或模型。",
                    }
                ],
            }
        value = {
            "id": payload["id"],
            "request": payload,
            "status": "running",
            "demo": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "limits": LIMITS[payload["mode"]],
            "message": "合成演示，无真实请求或费用。",
            "result": result,
        }
        self.runs[payload["id"]] = value
        return {k: v for k, v in value.items() if k != "result"}

    def status(self, identifier: str) -> dict[str, Any]:
        if identifier not in self.runs:
            raise ResearchUnavailable("没有该合成研究记录")
        return dict(self.runs[identifier], status="completed")


def configured_client() -> ResearchClient | None:
    url, token = os.getenv("RESEARCH_SERVICE_URL", ""), os.getenv("RESEARCH_SERVICE_TOKEN", "")
    if not url or not token:
        return None
    try:
        return ResearchClient(url, token, os.getenv("RESEARCH_SERVICE_SOCKET"))
    except ValueError:
        return None
