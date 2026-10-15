"""Host, origin and response-header boundary for every Web request."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from a_stock_tracker.web.settings import LOOPBACK, Settings

CSP = (
    "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
    "object-src 'none'; img-src 'self'"
)
# Retired workbench, publication and Flet session URLs are gone, not compatibility aliases.
RETIRED_EXACT = {"/research", "/discover", "/published", "/api/session"}
RETIRED_PREFIX = ("/research/", "/discover/", "/company/", "/jobs/", "/upload", "/opportunity/")
SAFE_METHODS = {"GET", "HEAD"}


def parse_host_port(netloc: str) -> tuple[str, int | None] | None:
    """Parse host and optional port strictly (RFC 3986), rejecting anything ambiguous."""
    netloc = netloc.strip().lower()
    if not netloc:
        return None
    if netloc.startswith("["):
        end = netloc.find("]")
        if end == -1:
            return None
        host, rest = netloc[1:end], netloc[end + 1 :]
        if rest == "":
            return host, None
        if not rest.startswith(":"):
            return None
        port_text = rest[1:]
    else:
        parts = netloc.split(":")
        if len(parts) == 1:
            return parts[0], None
        if len(parts) != 2:
            return None
        host, port_text = parts
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        return None
    return host, int(port_text)


class Origin:
    def __init__(self, settings: Settings):
        parsed = urlparse(settings.public_base_url)
        self.demo = settings.demo
        self.scheme = (parsed.scheme or "").lower()
        self.hostname = (parsed.hostname or "").lower()
        self.port = parsed.port or (443 if self.scheme == "https" else 80)

    def host_ok(self, header: str) -> bool:
        parsed = parse_host_port(header)
        if not parsed:
            return False
        host, port = parsed
        effective = port if port is not None else (443 if self.scheme == "https" else 80)
        if effective != self.port:
            return False
        return host in LOOPBACK if self.demo else host == self.hostname

    def url_ok(self, value: str, *, exact: bool) -> bool:
        try:
            parsed = urlparse(value.strip())
        except ValueError:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        if exact and (parsed.path or parsed.query or parsed.params or parsed.fragment):
            return False
        hp = parse_host_port(parsed.netloc)
        if not hp or parsed.scheme.lower() != self.scheme:
            return False
        host, port = hp
        effective = port if port is not None else (443 if self.scheme == "https" else 80)
        return host == self.hostname and effective == self.port

    def same_origin(self, headers: dict[str, str]) -> bool:
        """POST provenance: Sec-Fetch-Site, then Origin, then Referer; absence fails closed."""
        site = headers.get("sec-fetch-site", "").strip().lower()
        if site and site != "same-origin":
            return False
        origin = headers.get("origin", "").strip()
        if origin:
            return self.url_ok(origin, exact=True)
        referer = headers.get("referer", "").strip()
        if referer:
            return self.url_ok(referer, exact=False)
        return site == "same-origin"


class SecurityMiddleware:
    """Pure ASGI guard so it also covers static files and error responses."""

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.origin = Origin(settings)
        self.hsts = settings.secure_cookies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4404})
                return
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        if path in RETIRED_EXACT or path.startswith(RETIRED_PREFIX):
            await self.plain(send, 404, b"Not Found")
            return
        if not self.origin.host_ok(headers.get("host", "")):
            await self.plain(send, 403, b"Forbidden: Invalid Host header")
            return
        # TLS 0-RTT data can be replayed; never let it carry a side effect.
        if headers.get("early-data") == "1" and scope["method"] not in SAFE_METHODS:
            await self.plain(send, 425, b"Too Early")
            return
        static = path.startswith("/static/")

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                extra: list[tuple[bytes, bytes]] = [
                    (b"content-security-policy", CSP.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                ]
                if self.hsts:
                    extra.append((b"strict-transport-security", b"max-age=31536000"))
                existing = {k.lower() for k, _ in message.get("headers", [])}
                if b"cache-control" not in existing:
                    extra.append(
                        (
                            b"cache-control",
                            b"public, max-age=2592000, immutable" if static else b"no-store",
                        )
                    )
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        await self.app(scope, receive, with_headers)

    async def plain(self, send: Send, status: int, body: bytes) -> None:
        headers: list[Any] = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"cache-control", b"no-store"),
            (b"content-security-policy", CSP.encode()),
            (b"x-content-type-options", b"nosniff"),
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
