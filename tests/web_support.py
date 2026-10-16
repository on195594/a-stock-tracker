"""Shared fixtures for the server-rendered Web tests: synthetic clients only."""

from __future__ import annotations

import copy
import html
import re
from typing import Any

from fastapi.testclient import TestClient

from a_stock_tracker.auth import ACTIVE_SESSIONS
from a_stock_tracker.contracts import RankingPage, ReportList, RunState, ServiceInfo
from a_stock_tracker.research_client import DemoResearchClient
from a_stock_tracker.web import session as s
from a_stock_tracker.web.routes import create_app
from a_stock_tracker.web.settings import Settings

BASE = "https://stock.example"
OWNER = "424242"
SAME = {"origin": BASE}


def production(**overrides: Any) -> Settings:
    values = {
        "mode": "production",
        "public_base_url": BASE,
        "github_client_id": "client",
        "github_client_secret": "s" * 40,
        "allowed_user_id": OWNER,
        "session_secret": "s" * 40,
    }
    return Settings(**(values | overrides)).validate()


class FakeClient:
    """Programmable receiver client; records every submission."""

    def __init__(self, demo: DemoResearchClient | None = None):
        self.demo = demo or DemoResearchClient()
        self.submitted: list[dict[str, Any]] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.fail: dict[str, BaseException] = {}
        self.recent_runs: list[dict[str, Any]] | None = None
        self.recent_calls: list[dict[str, Any]] = []
        self.reports: dict[str, Any] | None = None

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise self.fail[name]

    def info(self) -> ServiceInfo:
        self._maybe_fail("info")
        return self.demo.info()

    def recent(
        self,
        mode: str | None = None,
        code: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> list[RunState]:
        self.recent_calls.append(dict(mode=mode, code=code, limit=limit, cursor=cursor))
        self._maybe_fail("recent")
        if self.recent_runs is not None:
            # Same filters as the receiver; programmed runs are already newest first.
            runs = [
                r
                for r in self.recent_runs
                if (not mode or r["request"]["mode"] == mode)
                and (not code or code in r["request"]["codes"])
            ]
            return [RunState.model_validate(r) for r in runs[:limit]]
        return self.demo.recent(mode, code, limit, cursor)

    def find_reports(self, code: str) -> ReportList:
        self._maybe_fail("find_reports")
        if self.reports is not None:
            return ReportList.model_validate(self.reports)
        return self.demo.find_reports(code)

    def submit(self, payload: dict[str, Any]) -> RunState:
        self.submitted.append(copy.deepcopy(payload))
        self._maybe_fail("submit")
        return self.demo.submit(payload)

    def status(self, identifier: str) -> RunState:
        self._maybe_fail("status")
        if identifier in self.runs:
            return RunState.model_validate(self.runs[identifier])
        return self.demo.status(identifier)

    def ranking(self, *args: Any) -> RankingPage:
        self._maybe_fail("ranking")
        return self.demo.ranking(*args)


def app_client(
    client: Any = None, settings: Settings | None = None, exchange: Any = None
) -> TestClient:
    settings = settings or production()
    app = create_app(
        settings,
        client if client is not None else FakeClient(),
        exchange=exchange or (lambda code, _settings: int(OWNER)),
    )
    return TestClient(app, base_url=settings.public_base_url)


def login(browser: TestClient) -> str:
    """Complete the real OAuth state round trip with a synthetic code exchange."""
    start = browser.get("/oauth/start", follow_redirects=False)
    state = re.search(r"state=([^&]+)", start.headers["location"]).group(1)
    done = browser.get(f"/oauth_callback?code=c&state={state}", follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/"
    return browser.cookies[s.COOKIE]


def csrf_of(page: str) -> str:
    return html.unescape(re.search(r'name="csrf" value="([^"]*)"', page).group(1))


def form_fields(page: str) -> dict[str, str]:
    return {k: html.unescape(v) for k, v in re.findall(r'name="(\w+)" value="([^"]*)"', page)}


def request_form(browser: TestClient, mode: str, code: str = "") -> dict[str, str]:
    page = browser.get(f"/analysis?mode={mode}&code={code}")
    assert page.status_code == 200
    fields = form_fields(page.text)
    fields.update(codes=code, confirmed="on")
    return fields


def reset_sessions() -> None:
    ACTIVE_SESSIONS.clear()
    s.OAUTH_STATES.clear()
