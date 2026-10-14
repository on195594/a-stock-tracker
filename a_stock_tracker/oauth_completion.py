"""One-use owner authorization from a completed, fresh Flet OAuth exchange."""

from flet.auth.authorization_service import AuthorizationService
from flet.auth.user import User

from a_stock_tracker.auth import Actor, AuthError, create_owner_actor


class OwnerAuthorization(AuthorizationService):
    """Keep OAuth/state/token handling in Flet; record only completed user identity."""

    _completed_user_id: str | int | None = None

    async def request_token(self, code: str) -> None:
        self._completed_user_id = None
        self.user = None
        await super().request_token(code)
        if self.fetch_user and isinstance(self.user, User):
            user_id = self.user.id
            if type(user_id) in (str, int):
                self._completed_user_id = user_id

    async def dehydrate_token(self, saved_token: str) -> None:
        # Restoring cached credentials must never create a fresh business session.
        self._completed_user_id = None
        await super().dehydrate_token(saved_token)


def consume_owner_actor(authorization: object, allowed_user_id: str | None) -> Actor:
    """Consume one completed exchange, including when owner validation fails."""
    if type(authorization) is not OwnerAuthorization:
        raise AuthError("fresh OAuth completion required")
    user_id = authorization._completed_user_id
    authorization._completed_user_id = None
    if user_id is None:
        raise AuthError("fresh OAuth completion required")
    return create_owner_actor(user_id, allowed_user_id)
