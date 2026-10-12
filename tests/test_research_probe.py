"""Deployment readiness reads /info only, never research or private responses."""

import httpx
import pytest

from a_stock_tracker import research_client
from a_stock_tracker.research_service import LIMITS


@pytest.mark.parametrize("available", [True, False])
def test_probe_checks_authenticated_info_only(monkeypatch, capsys, available):
    client = research_client.ResearchClient("http://research", "t" * 32, "/synthetic/socket")
    calls = []

    def call(method, path, payload=None):
        calls.append((method, path, payload))
        return {"demo": False, "modes": {m: {"available": available} for m in LIMITS}}

    monkeypatch.setattr(client, "call", call)
    monkeypatch.setattr(research_client, "configured_client", lambda: client)
    assert research_client.check_bridge() == 0
    assert calls == [("GET", "/info", None)]
    assert "no research was started" in capsys.readouterr().out


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"demo": True, "modes": {}},
        {"demo": False, "modes": {}},
        {"demo": False, "modes": {"screen": None}},
    ],
)
def test_probe_rejects_invalid_info(monkeypatch, info):
    client = research_client.ResearchClient("http://research", "t" * 32, "/synthetic/socket")
    monkeypatch.setattr(client, "info", lambda: info)
    monkeypatch.setattr(research_client, "configured_client", lambda: client)
    assert research_client.check_bridge() == 1


@pytest.mark.parametrize(
    "client", [None, research_client.ResearchClient("http://research", "t" * 32)]
)
def test_probe_requires_configured_socket(monkeypatch, client):
    monkeypatch.setattr(research_client, "configured_client", lambda: client)
    assert research_client.check_bridge() == 1


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError, httpx.TimeoutException, ValueError, research_client.ResearchUnavailable],
)
def test_probe_never_prints_sensitive_errors(monkeypatch, capsys, error):
    secret = "synthetic-private-token-or-response"
    client = research_client.ResearchClient("http://research", "t" * 32, "/synthetic/socket")

    def fail():
        raise error(secret)

    monkeypatch.setattr(client, "info", fail)
    monkeypatch.setattr(research_client, "configured_client", lambda: client)
    assert research_client.check_bridge() == 1
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


@pytest.mark.parametrize("status", [200, 403, 500])
def test_probe_uses_socket_and_auth_without_exposing_response(monkeypatch, capsys, status):
    token = "synthetic-private-token-" + "t" * 32
    monkeypatch.setenv("RESEARCH_SERVICE_URL", "http://research")
    monkeypatch.setenv("RESEARCH_SERVICE_TOKEN", token)
    monkeypatch.setenv("RESEARCH_SERVICE_SOCKET", "/synthetic/socket")
    requests = []
    sockets = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={
                "demo": False,
                "modes": {m: {"available": True} for m in LIMITS},
                "private-test-field": token,
            },
        )

    def transport(*, uds):
        sockets.append(uds)
        return httpx.MockTransport(handler)

    monkeypatch.setattr(httpx, "HTTPTransport", transport)
    assert research_client.check_bridge() == (0 if status == 200 else 1)
    assert sockets == ["/synthetic/socket"]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == "http://research/info"
    assert requests[0].headers["authorization"] == "Bearer " + token
    assert not requests[0].content
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
