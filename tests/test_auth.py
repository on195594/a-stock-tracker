"""Unit tests for actor authentication, session token signing and verification."""

import base64
import time

from a_stock_tracker import auth


def test_session_token_roundtrip():
    secret = "test-secret-key-12345"
    user_id = "12345678"
    expires_at = time.time() + 3600
    token = auth.sign_session_token(user_id, secret, expires_at)
    assert isinstance(token, str) and "." in token

    actor = auth.verify_session_token(token, secret, user_id)
    assert actor is not None
    assert actor.user_id == user_id
    assert actor.role == "owner"
    assert actor.is_valid


def test_session_token_tampering():
    secret = "test-secret-key-12345"
    user_id = "12345678"
    token = auth.sign_session_token(user_id, secret, time.time() + 3600)
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
    expired_token = auth.sign_session_token(user_id, secret, time.time() - 10)
    assert auth.verify_session_token(expired_token, secret, user_id) is None


def test_session_token_unauthorized_user():
    secret = "test-secret-key-12345"
    token = auth.sign_session_token("99999999", secret, time.time() + 3600)
    assert auth.verify_session_token(token, secret, "12345678") is None


def test_session_token_malformed_inputs():
    secret = "test-secret-key-12345"
    for bad in ["", None, "no-dot", "a.b.c", "...", "invalid.sig"]:
        assert auth.verify_session_token(bad, secret, "12345678") is None


def test_parse_cookie_header():
    assert auth.parse_cookie_header(None) == {}
    assert auth.parse_cookie_header("") == {}
    header = "flet_oauth_state=xyz; stock_session=token123; other=val=ue"
    cookies = auth.parse_cookie_header(header)
    assert cookies["flet_oauth_state"] == "xyz"
    assert cookies["stock_session"] == "token123"
    assert cookies["other"] == "val=ue"


def test_session_revocation_invalidates_token():
    secret = "test-secret-key-12345"
    user_id = "12345678"
    token = auth.sign_session_token(user_id, secret, time.time() + 3600)
    actor = auth.verify_session_token(token, secret, user_id)
    assert actor is not None and actor.is_valid

    # Revoking session directly removes it from active registry
    auth.revoke_session(actor.sid)
    # Actor with this sid automatically becomes invalid
    assert not actor.is_valid
    # Token verification now returns None
    assert auth.verify_session_token(token, secret, user_id) is None


def test_revoke_session_explicitly():
    secret = "test-secret-key-12345"
    user_id = "12345678"
    token = auth.sign_session_token(user_id, secret, time.time() + 3600)
    assert auth.verify_session_token(token, secret, user_id) is not None

    auth.revoke_session(token)
    assert auth.verify_session_token(token, secret, user_id) is None

    # Malformed unhashable sid payload does not raise TypeError
    bad = base64.urlsafe_b64encode(b'{"sid":["unhashable"]}').decode() + ".fake"
    auth.revoke_session(bad)


def test_exchange_ticket_lifecycle():
    user_id = "12345678"
    sid = "test-session-id"
    ticket = auth.create_exchange_ticket(user_id, sid)
    assert isinstance(ticket, str) and len(ticket) >= 16

    # Redeem once succeeds, returning (user_id, sid)
    assert auth.redeem_exchange_ticket(ticket) == (user_id, sid)

    # Second redemption fails (single-use)
    assert auth.redeem_exchange_ticket(ticket) is None

    # Invalid / empty tickets
    assert auth.redeem_exchange_ticket(None) is None
    assert auth.redeem_exchange_ticket("nonexistent") is None

    # Test revoke_exchange_tickets by sid
    t2 = auth.create_exchange_ticket(user_id, sid)
    # Anonymous flush refused (does not purge)
    auth.revoke_exchange_tickets(None)
    assert t2 in auth.EXCHANGE_TICKETS

    # Specific sid flush purges tickets
    auth.revoke_exchange_tickets(sid)
    assert auth.redeem_exchange_ticket(t2) is None


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
