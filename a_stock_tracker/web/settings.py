"""Environment configuration for the server-rendered Web; production fails closed."""

from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from a_stock_tracker.auth import check_production_auth_config

LOOPBACK = ("127.0.0.1", "localhost", "::1")
ADMIN_SCOPES = ("local", "any")
MIN_ADMIN_PASSWORD = 16


@dataclass(frozen=True)
class Settings:
    mode: Literal["demo", "production"]
    public_base_url: str
    host: str = "127.0.0.1"
    port: int = 8550
    github_client_id: str = ""
    github_client_secret: str = ""
    allowed_user_id: str = ""
    # Optional password login for the owner; an empty password keeps it off.
    admin_username: str = ""
    admin_password: str = ""
    admin_scope: str = "local"
    # Extra public addresses or networks treated as local, e.g. this host's egress IP.
    admin_allowed_ips: tuple[str, ...] = ()
    # Demo sessions are signed with a per-process secret and never outlive the process.
    session_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))

    @property
    def demo(self) -> bool:
        return self.mode == "demo"

    @property
    def secure_cookies(self) -> bool:
        return urlparse(self.public_base_url).scheme == "https"

    def validate(self) -> Settings:
        parsed = urlparse(self.public_base_url)
        origin_only = parsed.path in ("", "/")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not origin_only:
            raise ValueError("PUBLIC_BASE_URL must be an origin such as https://example.org")
        if self.demo:
            if self.host not in LOOPBACK or parsed.hostname not in LOOPBACK:
                raise ValueError("Demo mode only allows a loopback host and public origin")
        else:
            check_production_auth_config(
                self.github_client_id, self.github_client_secret, self.allowed_user_id
            )
            if parsed.scheme != "https":
                raise ValueError("Production requires an https PUBLIC_BASE_URL")
        if self.admin_scope not in ADMIN_SCOPES:
            raise ValueError("ADMIN_LOGIN_SCOPE must be 'local' or 'any'")
        for network in self.admin_allowed_ips:
            ipaddress.ip_network(network, strict=False)  # ValueError on a malformed entry
        if self.admin_password and (
            not self.admin_username.strip() or len(self.admin_password) < MIN_ADMIN_PASSWORD
        ):
            raise ValueError(
                f"ADMIN_PASSWORD needs ADMIN_USERNAME and at least {MIN_ADMIN_PASSWORD} characters"
            )
        return self


def settings_from_env() -> Settings:
    raw = os.getenv("APP_MODE", "demo")
    if raw not in ("demo", "production"):
        raise ValueError(f"Invalid APP_MODE: {raw!r}. Must be 'demo' or 'production'.")
    mode: Literal["demo", "production"] = "production" if raw == "production" else "demo"
    port = int(os.getenv("PORT", "8550"))
    secret = os.getenv("GITHUB_CLIENT_SECRET", "")
    return Settings(
        mode=mode,
        public_base_url=os.getenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{port}").rstrip("/"),
        host=os.getenv("HOST", "127.0.0.1"),
        port=port,
        github_client_id=os.getenv("GITHUB_CLIENT_ID", ""),
        github_client_secret=secret,
        allowed_user_id=os.getenv("GITHUB_ALLOWED_USER_ID", ""),
        admin_username=os.getenv("ADMIN_USERNAME", ""),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
        admin_scope=os.getenv("ADMIN_LOGIN_SCOPE", "local"),
        admin_allowed_ips=tuple(
            item.strip() for item in os.getenv("ADMIN_ALLOWED_IPS", "").split(",") if item.strip()
        ),
        **({"session_secret": secret} if mode == "production" else {}),
    ).validate()
