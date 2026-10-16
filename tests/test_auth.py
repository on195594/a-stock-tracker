"""Unit tests for actor authentication, session token signing and verification."""

import time

from a_stock_tracker import auth


def test_session_token_roundtrip():
    secret = "test-secret-key-12345"
    user_id = "12345678"
    expires_at = time.time() + 3600
    sid = auth.register_session(user_id, expires_at)
    token = auth.sign_session_token(user_id, secret, expires_at, sid=sid)
    assert isinstance(token, str) and "." in token

    actor = auth.verify_session_token(token, secret, user_id)
    assert actor is not None
    assert actor.user_id == user_id
    assert actor.sid == sid


def test_session_token_tampering():
    secret = "test-secret-key-12345"
    user_id = "12345678"
    expires_at = time.time() + 3600
    sid = auth.register_session(user_id, expires_at)
    token = auth.sign_session_token(user_id, secret, expires_at, sid=sid)
    b64_payload, sig = token.rsplit(".", 1)

    # Tampered signature
    assert auth.verify_session_token(f"{b64_payload}.{'0' * len(sig)}", secret, user_id) is None

    # Tampered payload
    assert auth.verify_session_token(f"badpayload.{sig}", secret, user_id) is None

    # Wrong secret
    assert auth.verify_session_token(token, "wrong-secret", user_id) is None


def test_session_token_expired():
    secret = "test-secret-key-12345"
    user_id = "12345678"
    expires_at = time.time() - 10
    sid = auth.register_session(user_id, expires_at)
    expired_token = auth.sign_session_token(user_id, secret, expires_at, sid=sid)
    assert auth.verify_session_token(expired_token, secret, user_id) is None


def test_session_token_unauthorized_user():
    secret = "test-secret-key-12345"
    expires_at = time.time() + 3600
    sid = auth.register_session("99999999", expires_at)
    token = auth.sign_session_token("99999999", secret, expires_at, sid=sid)
    assert auth.verify_session_token(token, secret, "12345678") is None


def test_session_token_malformed_inputs():
    secret = "test-secret-key-12345"
    for bad in ["", None, "no-dot", "a.b.c", "...", "invalid.sig"]:
        assert auth.verify_session_token(bad, secret, "12345678") is None


def test_session_revocation_invalidates_token():
    secret = "test-secret-key-12345"
    user_id = "12345678"
    expires_at = time.time() + 3600
    sid = auth.register_session(user_id, expires_at)
    token = auth.sign_session_token(user_id, secret, expires_at, sid=sid)
    actor = auth.verify_session_token(token, secret, user_id)
    assert actor is not None

    # Revoking session directly removes it from active registry
    auth.revoke_session(actor.sid)
    # Token verification now returns None
    assert auth.verify_session_token(token, secret, user_id) is None


def test_fresh_login_replaces_prior_user_sessions():
    user_id = "12345678"
    secret = "test-secret-12345"

    sid1 = auth.register_session(user_id, time.time() + 3600)
    token1 = auth.sign_session_token(user_id, secret, time.time() + 3600, sid=sid1)
    assert auth.verify_session_token(token1, secret, user_id) is not None

    # Fresh login for the same user must revoke sid1
    sid2 = auth.register_session(user_id, time.time() + 3600)
    token2 = auth.sign_session_token(user_id, secret, time.time() + 3600, sid=sid2)

    assert sid1 != sid2
    assert auth.verify_session_token(token1, secret, user_id) is None
    assert auth.verify_session_token(token2, secret, user_id) is not None
