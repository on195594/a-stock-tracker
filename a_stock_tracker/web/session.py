"""Signed session cookie, CSRF tokens, the GitHub OAuth exchange and optional admin login."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx
from starlette.requests import Request
from starlette.responses import Response

from a_stock_tracker.auth import (
    AUTH_EXPIRY_SECONDS,
    Actor,
    AuthError,
    create_owner_actor,
    register_session,
    revoke_session,
    sign_session_token,
    verify_session_token,
)
from a_stock_tracker.web.settings import Settings

COOKIE = "stock_session"
STATE_COOKIE = "stock_oauth_state"
LOGIN_CSRF_COOKIE = "stock_login_csrf"
STATE_SECONDS = 600
MAX_OAUTH_STATES = 128
DEMO_USER = "0"
# One-time OAuth states: state -> expiry (wall clock).
OAUTH_STATES: dict[str, float] = {}


def allowed_user(settings: Settings) -> str:
    return DEMO_USER if settings.demo else settings.allowed_user_id


def client_ip(request: Request) -> str:
    # Nginx overwrites X-Real-IP with the client address (Cloudflare's CF-Connecting-IP only
    # for Cloudflare peers); only host-local peers reach the app directly.
    forwarded = request.headers.get("x-real-ip", "").strip()
    return forwarded or (request.client.host if request.client else "")


def is_local(request: Request, settings: Settings) -> bool:
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address
        address = ipaddress.ip_address(client_ip(request))
    except ValueError:
        return False
    # ::ffff:a.b.c.d must match IPv4 allowlist entries.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address.is_loopback
        or address.is_private
        or any(
            address in ipaddress.ip_network(net, strict=False) for net in settings.admin_allowed_ips
        )
    )


def admin_enabled(request: Request, settings: Settings) -> bool:
    if settings.demo or not settings.admin_password:
        return False
    return settings.admin_scope == "any" or is_local(request, settings)


def issue_login_csrf(response: Response, settings: Settings, token: str) -> None:
    """Pre-login CSRF: the form echoes a token that must match this browser's cookie."""
    set_cookie(response, settings, LOGIN_CSRF_COOKIE, token, STATE_SECONDS)


def login_csrf_ok(request: Request, supplied: Any) -> bool:
    bound = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    return isinstance(supplied, str) and bool(bound) and hmac.compare_digest(supplied, bound)


def admin_ok(settings: Settings, username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username.encode(), settings.admin_username.encode())
    password_ok = hmac.compare_digest(password.encode(), settings.admin_password.encode())
    return user_ok and password_ok


def current_actor(request: Request, settings: Settings) -> Actor | None:
    """Re-verified on every call: signature, owner whitelist and live server-side session."""
    return verify_session_token(
        request.cookies.get(COOKIE), settings.session_secret, allowed_user(settings)
    )


def csrf_token(actor: Actor, settings: Settings) -> str:
    return hmac.new(
        settings.session_secret.encode(), b"csrf:" + (actor.sid or "").encode(), hashlib.sha256
    ).hexdigest()


def csrf_ok(actor: Actor, settings: Settings, supplied: Any) -> bool:
    return isinstance(supplied, str) and hmac.compare_digest(supplied, csrf_token(actor, settings))


def set_cookie(response: Response, settings: Settings, name: str, value: str, age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


def clear_cookie(response: Response, settings: Settings, name: str = COOKIE) -> None:
    response.delete_cookie(
        name, path="/", httponly=True, samesite="lax", secure=settings.secure_cookies
    )


def start_session(response: Response, settings: Settings, user_id: str) -> None:
    expires_at = time.time() + AUTH_EXPIRY_SECONDS
    sid = register_session(user_id, expires_at)
    token = sign_session_token(user_id, settings.session_secret, expires_at, sid=sid)
    set_cookie(response, settings, COOKIE, token, AUTH_EXPIRY_SECONDS)


def end_session(request: Request, response: Response, settings: Settings) -> None:
    actor = current_actor(request, settings)
    if actor is not None:
        revoke_session(actor.sid)
    clear_cookie(response, settings)


def purge_states(now: float | None = None) -> None:
    now = time.time() if now is None else now
    for state in [s for s, expiry in OAUTH_STATES.items() if expiry <= now]:
        OAUTH_STATES.pop(state, None)


def authorize_redirect(response_factory: Callable[[str], Response], settings: Settings) -> Response:
    purge_states()
    if len(OAUTH_STATES) >= MAX_OAUTH_STATES:
        return Response("登录请求过多，请稍后重试。", status_code=429, media_type="text/plain")
    state = secrets.token_urlsafe(24)
    OAUTH_STATES[state] = time.time() + STATE_SECONDS
    url = "https://github.com/login/oauth/authorize?" + urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.public_base_url + "/oauth_callback",
            "state": state,
            "allow_signup": "false",
        }
    )
    response = response_factory(url)
    # Bind the state to this browser so a callback link cannot log someone else in.
    set_cookie(response, settings, STATE_COOKIE, state, STATE_SECONDS)
    return response


def consume_state(request: Request) -> bool:
    """One-time: the state is removed whether or not the rest of the exchange succeeds."""
    purge_states()
    state = request.query_params.get("state", "")
    bound = request.cookies.get(STATE_COOKIE, "")
    expiry = OAUTH_STATES.pop(state, None) if state else None
    return bool(
        expiry is not None and expiry > time.time() and bound and hmac.compare_digest(state, bound)
    )


def github_user_id(code: str, settings: Settings) -> Any:
    """Exchange the authorization code; the token is used once and never stored."""
    with httpx.Client(timeout=10, trust_env=False) as client:
        token = client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": settings.public_base_url + "/oauth_callback",
            },
        )
        token.raise_for_status()
        access = token.json().get("access_token")
        if not isinstance(access, str) or not access:
            raise AuthError("OAuth exchange returned no token")
        user = client.get(
            "https://api.github.com/user",
            headers={"Authorization": "Bearer " + access, "Accept": "application/vnd.github+json"},
        )
        user.raise_for_status()
        return user.json().get("id")


def owner_from_code(
    code: str, settings: Settings, exchange: Callable[[str, Settings], Any]
) -> Actor:
    if not code:
        raise AuthError("missing authorization code")
    try:
        user_id = exchange(code, settings)
    except (httpx.HTTPError, ValueError) as exc:
        raise AuthError("OAuth exchange failed") from exc
    if type(user_id) not in (str, int):
        raise AuthError("OAuth profile has no numeric identity")
    return create_owner_actor(user_id, settings.allowed_user_id)
