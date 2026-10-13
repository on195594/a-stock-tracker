"""Research API client; no market/model credentials or execution in Web."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from a_stock_tracker.opportunity_demo import demo_board, demo_report
from a_stock_tracker.research_service import LIMITS, quote, result_summary, validate_request


class ResearchUnavailable(Exception):
    pass


class ResearchRejected(ResearchUnavailable):
    """The receiver explicitly refused execution; inputs can be edited safely."""


class ResearchQuoteChanged(ResearchRejected):
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
                if response.status_code in {400, 403, 404, 409, 413, 503}:
                    try:
                        data = response.json()
                        detail = data.get("detail")
                        if isinstance(detail, str) and len(detail) <= 100:
                            error = (
                                ResearchQuoteChanged
                                if data.get("code") == "quote_changed"
                                else ResearchUnavailable
                                if response.status_code == 503
                                else ResearchRejected
                            )
                            raise error(detail)
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
        runs = []
        for identifier in reversed(self.runs):
            if any(s.get("continued_from") == identifier for s in self.runs.values()):
                continue
            state = self.status(identifier)
            state["summary"] = result_summary(state.pop("result"))
            count, node = 0, state
            while node.get("continued_from"):
                count += 1
                node = self.runs[node["continued_from"]]
            state["continuations"] = count
            runs.append(state)
            if len(runs) == 10:
                break
        return runs

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        validate_request(payload)
        if payload["id"] in self.runs:
            if self.runs[payload["id"]]["request"] != payload:
                raise ResearchUnavailable("原请求不能更改范围")
            return self.status(payload["id"])
        remaining = 1 if payload.get("resume_from") else None
        if payload["quote"] != quote(self.info(), payload["mode"], remaining):
            raise ResearchQuoteChanged("预算或模型已变化，请重新确认")
        if payload.get("from") and (
            payload["from"] not in self.runs
            or self.runs[payload["from"]]["request"]["mode"] != "screen"
        ):
            raise ResearchRejected("来源初筛不存在")
        if payload["mode"] == "swing":
            board = demo_board(
                {"600002.SH": "wide", "600003.SH": "gap", "600004.SH": "zero"}.get(
                    payload["codes"][0], "reference"
                )
            )
            for row in board["rows"]:
                row["code"] = payload["codes"][0]
            result = {"board": board}
        else:
            result = {
                "coverage": {
                    "mode": "market" if payload["mode"] == "screen" else "sample",
                    "prefilter": payload["quote"].get("prefilter"),
                    "prefilter_counts": {"excluded": 1, "pending": 5, "gap": 0},
                    "data_date": "合成日期",
                    "status": "partial",
                    "total": 6 if payload["mode"] == "screen" else 1,
                    "counts": {
                        "qualified": 4 if payload["mode"] == "screen" else 1,
                        "excluded": (2 if payload.get("resume_from") else 1)
                        if payload["mode"] == "screen"
                        else 0,
                        "pending": 1
                        if payload["mode"] == "screen" and not payload.get("resume_from")
                        else 0,
                    },
                },
                "candidates": [
                    {
                        "code": (payload["codes"] or ["600001.SH"])[0],
                        "name": "演示公司",
                        "industry": "合成行业",
                        "pb": 1.0,
                        "total_mv": 600000,
                        "roe_mean": 10.0,
                    }
                ],
                "reports": []
                if payload["mode"] == "screen"
                else [demo_report(payload["codes"][0])],
            }
        if payload["mode"] == "screen":
            result["groups"] = [
                {
                    "industry": industry,
                    "periods": ["2023-12-31", "2024-12-31", "2025-12-31"],
                    "ranking": [
                        {
                            "code": f"60000{i}.SH",
                            "name": name,
                            "pb": 1.0,
                            "roe_mean": 10.0,
                            "total_mv": 600000,
                            "order": 1.0,
                            "pb_rank": 1.0,
                            "roe_rank": 1.0,
                        }
                    ],
                }
                for i, (industry, name) in enumerate(
                    [
                        ("合成银行", "演示公司"),
                        ("合成制造", "较大距离示例"),
                        ("合成能源", "资料缺口示例"),
                        ("合成消费", "无资料示例"),
                    ],
                    1,
                )
            ]
            result["truncated"] = False
            result["usage"] = {
                # A first pass stops on its synthetic budget; a continuation finishes the pool.
                "requests": 2 if remaining else payload["quote"]["requests"],
                "request_limit": payload["quote"]["requests"],
                "seconds_used": 1.0,
                "stop_reason": "completed" if remaining else "request_budget",
                "newly_covered": 1,
                "covered": 6 if remaining else 5,
                "remaining": 0 if remaining else 1,
            }
        value = {
            "id": payload["id"],
            "request": payload,
            "status": "running",
            "demo": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "limits": payload["quote"],
            "message": "合成演示，无真实请求或费用。",
            "result": result,
        }
        if payload.get("resume_from"):
            value["continued_from"] = payload["resume_from"]
        self.runs[payload["id"]] = value
        return {k: v for k, v in value.items() if k != "result"}

    def status(self, identifier: str) -> dict[str, Any]:
        if identifier not in self.runs:
            raise ResearchUnavailable("没有该合成研究记录")
        value = self.runs[identifier]
        pending = value["result"].get("coverage", {}).get("counts", {}).get("pending", 0)
        successor = next(
            (r["id"] for r in self.runs.values() if r.get("continued_from") == identifier), None
        )
        return dict(
            value,
            status="partial" if pending else "completed",
            can_continue=bool(pending) and not successor,
            continued_by=successor,
            continuation_quote=quote(self.info(), "screen", pending) if pending else None,
        )


def configured_client() -> ResearchClient | None:
    url, token = os.getenv("RESEARCH_SERVICE_URL", ""), os.getenv("RESEARCH_SERVICE_TOKEN", "")
    if not url or not token:
        return None
    try:
        return ResearchClient(url, token, os.getenv("RESEARCH_SERVICE_SOCKET"))
    except ValueError:
        return None


def check_bridge() -> int:
    """No-cost deployment probe; never print credentials, responses or exceptions."""
    try:
        client = configured_client()
        if client is None or not client.socket:
            raise ValueError("Missing private bridge")
        info = client.info()
        if info.get("demo") is not False or not isinstance(info.get("modes"), dict):
            raise ValueError("Invalid bridge response")
        for mode in LIMITS:
            if not isinstance(info["modes"].get(mode, {}).get("available"), bool):
                raise ValueError("Invalid bridge mode")
    except Exception:
        print("Research bridge unavailable; check receiver, socket mount and shared token.")
        return 1
    print("Research bridge authenticated; no research was started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(check_bridge())
