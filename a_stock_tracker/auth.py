"""Authentication and actor authorization boundaries."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

AUTH_EXPIRY_SECONDS = 8 * 3600  # 8 hours maximum business authorization


class AuthError(RuntimeError):
    pass


@dataclass
class Actor:
    user_id: str
    sid: str | None = None


def create_owner_actor(github_user_id: str | int | None, allowed_user_id: str | None) -> Actor:
    """Authenticate GitHub user ID against the server-side numeric ID whitelist."""
    if github_user_id is None:
        raise AuthError("missing GitHub user ID")
    if allowed_user_id is None:
        raise AuthError("missing allowed user ID configuration")

    norm_user = str(github_user_id).strip()
    norm_allowed = str(allowed_user_id).strip()

    if not norm_user.isdigit() or not norm_allowed.isdigit():
        raise AuthError("GitHub user ID must be numeric")

    if norm_user != norm_allowed:
        raise AuthError("unauthorized GitHub user ID")

    return Actor(user_id=norm_user)


def check_production_auth_config(
    client_id: str | None,
    client_secret: str | None,
    allowed_user_id: str | None,
) -> None:
    """Validate production OAuth environment variables; fail fast if missing or invalid."""
    if not client_id or not client_id.strip():
        raise AuthError("Production mode requires GITHUB_CLIENT_ID")
    if not client_secret or not client_secret.strip():
        raise AuthError("Production mode requires GITHUB_CLIENT_SECRET")
    if not allowed_user_id or not allowed_user_id.strip():
        raise AuthError("Production mode requires GITHUB_ALLOWED_USER_ID")
    if not allowed_user_id.strip().isdigit():
        raise AuthError("GITHUB_ALLOWED_USER_ID must be numeric")


# Active server-side sessions: sid -> (user_id, expires_at)
ACTIVE_SESSIONS: dict[str, tuple[str, float]] = {}


def purge_expired_sessions() -> None:
    now = time.time()
    expired_sids = [sid for sid, (_, exp) in ACTIVE_SESSIONS.items() if exp <= now]
    for sid in expired_sids:
        ACTIVE_SESSIONS.pop(sid, None)


def register_session(user_id: str, expires_at: float) -> str:
    """Register an active server-side session SID for an authenticated user, replacing any prior sessions for that user."""
    purge_expired_sessions()
    norm_user = str(user_id).strip()
    for old_sid in [s for s, (u, _) in list(ACTIVE_SESSIONS.items()) if u == norm_user]:
        ACTIVE_SESSIONS.pop(old_sid, None)
    sid = secrets.token_hex(16)
    ACTIVE_SESSIONS[sid] = (norm_user, expires_at)
    return sid


def sign_session_token(user_id: str, secret: str, expires_at: float, sid: str) -> str:
    """Create an HMAC-SHA256 signed session token for persistent browser authentication."""
    purge_expired_sessions()
    norm_user = str(user_id).strip()
    payload = json.dumps(
        {"u": norm_user, "sid": sid, "exp": int(expires_at)}, separators=(",", ":")
    ).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), b64_payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64_payload}.{sig}"


def revoke_session(sid: str | None) -> None:
    """Explicitly revoke a session sid from active registry."""
    if isinstance(sid, str) and sid.strip():
        ACTIVE_SESSIONS.pop(sid.strip(), None)


def verify_session_token(
    token: str | None, secret: str, allowed_user_id: str | None
) -> Actor | None:
    """Verify signed session token against secret, owner whitelist, and active registry; returns valid Actor or None."""
    purge_expired_sessions()
    if not token or "." not in token or not secret or not allowed_user_id:
        return None
    try:
        b64_payload, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(
            secret.encode("utf-8"), b64_payload.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padded = b64_payload + "=" * (-len(b64_payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        user_id = str(data.get("u", "")).strip()
        sid = str(data.get("sid", "")).strip()
        exp = float(data.get("exp", 0))
        now = time.time()
        if exp <= now:
            return None
        norm_allowed = str(allowed_user_id).strip()
        if not user_id.isdigit() or user_id != norm_allowed:
            return None
        if sid not in ACTIVE_SESSIONS:
            return None
        active_user, active_exp = ACTIVE_SESSIONS[sid]
        if active_user != user_id or active_exp <= now:
            ACTIVE_SESSIONS.pop(sid, None)
            return None
        return Actor(user_id=user_id, sid=sid)
    except Exception:
        return None
