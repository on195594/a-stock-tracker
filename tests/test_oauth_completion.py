"""Offline lifecycle tests; no OAuth endpoints or client event dispatch."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from flet.auth.authorization_service import AuthorizationService
from flet.auth.providers import GitHubOAuthProvider
from flet.auth.user import User

from a_stock_tracker.auth import AuthError
from a_stock_tracker.oauth_completion import OwnerAuthorization, consume_owner_actor


def authorization(*, fetch_user=True):
    return OwnerAuthorization(
        GitHubOAuthProvider("test-client", "test-secret", "http://localhost/callback"),
        fetch_user=fetch_user,
        fetch_groups=False,
    )


def complete(auth, user_id="123"):
    async def fetched_user(code):
        auth.user = User({"id": user_id}, user_id)

    with patch.object(AuthorizationService, "request_token", side_effect=fetched_user) as request:
        asyncio.run(auth.request_token("synthetic-code"))
        request.assert_awaited_once_with("synthetic-code")


def test_success_is_consumed_once_and_uses_completed_identity():
    auth = authorization()
    complete(auth)
    auth.user = User({"id": "999"}, "999")
    actor = consume_owner_actor(auth, "123")
    assert actor.user_id == "123"
    assert actor.is_valid
    with pytest.raises(AuthError, match="fresh OAuth completion required"):
        consume_owner_actor(auth, "123")


def test_owner_rejection_also_consumes_completion():
    auth = authorization()
    complete(auth)
    with pytest.raises(AuthError, match="unauthorized"):
        consume_owner_actor(auth, "999")
    with pytest.raises(AuthError, match="fresh OAuth completion required"):
        consume_owner_actor(auth, "123")


@pytest.mark.parametrize("auth", [None, MagicMock(), MagicMock(spec=OwnerAuthorization)])
def test_unrelated_objects_are_not_completion_proof(auth):
    with pytest.raises(AuthError, match="fresh OAuth completion required"):
        consume_owner_actor(auth, "123")


def test_cached_user_without_exchange_is_insufficient():
    auth = authorization()
    auth.user = User({"id": "123"}, "123")
    with pytest.raises(AuthError, match="fresh OAuth completion required"):
        consume_owner_actor(auth, "123")


@pytest.mark.parametrize("failure", [None, RuntimeError("exchange failed")])
def test_failed_or_incomplete_exchange_clears_previous_completion(failure):
    auth = authorization()
    complete(auth)
    with patch.object(AuthorizationService, "request_token", new=AsyncMock(side_effect=failure)):
        if failure:
            with pytest.raises(RuntimeError, match="exchange failed"):
                asyncio.run(auth.request_token("synthetic-code"))
        else:
            asyncio.run(auth.request_token("synthetic-code"))
    assert auth.user is None
    with pytest.raises(AuthError, match="fresh OAuth completion required"):
        consume_owner_actor(auth, "123")


def test_no_completion_while_exchange_is_in_flight():
    auth = authorization()

    async def fetched_user(code):
        auth.user = User({"id": "123"}, "123")
        with pytest.raises(AuthError, match="fresh OAuth completion required"):
            consume_owner_actor(auth, "123")

    with patch.object(AuthorizationService, "request_token", side_effect=fetched_user):
        asyncio.run(auth.request_token("synthetic-code"))
    assert consume_owner_actor(auth, "123").is_valid


def test_user_fetch_must_be_enabled():
    auth = authorization(fetch_user=False)
    complete(auth)
    with pytest.raises(AuthError, match="fresh OAuth completion required"):
        consume_owner_actor(auth, "123")


def test_hydration_clears_completion_and_never_creates_it():
    auth = authorization()
    complete(auth)
    with patch.object(AuthorizationService, "dehydrate_token", new=AsyncMock()) as hydrate:
        asyncio.run(auth.dehydrate_token("synthetic-saved-token"))
        hydrate.assert_awaited_once_with("synthetic-saved-token")
    with pytest.raises(AuthError, match="fresh OAuth completion required"):
        consume_owner_actor(auth, "123")


def test_token_retrieval_does_not_create_completion():
    auth = authorization()
    auth.user = User({"id": "123"}, "123")
    assert asyncio.run(auth.get_token()) is None
    with pytest.raises(AuthError, match="fresh OAuth completion required"):
        consume_owner_actor(auth, "123")


def test_profile_failure_cannot_leave_completion_even_with_user_populated():
    auth = authorization()

    async def failed_profile(code):
        auth.user = User({"id": "123"}, "123")
        raise RuntimeError("profile failed")

    with patch.object(AuthorizationService, "request_token", side_effect=failed_profile):
        with pytest.raises(RuntimeError, match="profile failed"):
            asyncio.run(auth.request_token("synthetic-code"))
    with pytest.raises(AuthError, match="fresh OAuth completion required"):
        consume_owner_actor(auth, "123")


def test_new_exchange_can_authorize_again_after_consumption():
    auth = authorization()
    complete(auth)
    first = consume_owner_actor(auth, "123")
    first.revoke()
    complete(auth)
    second = consume_owner_actor(auth, "123")
    assert second.is_valid
    assert not first.is_valid
