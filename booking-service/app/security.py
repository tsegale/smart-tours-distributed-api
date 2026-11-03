"""Caller identity, as asserted by the API gateway.

The gateway verifies the JWT, strips any client-supplied X-User-* headers and sets its own.
That makes these headers trustworthy only because this service is not reachable from outside
the internal network (docker-compose publishes no port for it). Never take user ids from the
request body: that was an IDOR hole in the first version of this service.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header

from .errors import ForbiddenError, UnauthenticatedError


@dataclass(frozen=True)
class CurrentUser:
    id: str
    roles: frozenset[str]
    # The token's email claim, forwarded by the gateway as X-User-Email; None when the token has none.
    email: str | None = None

    def has_any_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


def get_current_user(
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_roles: Annotated[str | None, Header()] = None,
    x_user_email: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not x_user_id:
        raise UnauthenticatedError("Missing X-User-Id; requests must come through the API gateway")
    roles = frozenset(r.strip() for r in (x_user_roles or "").split(",") if r.strip())
    email = x_user_email.strip() if x_user_email and x_user_email.strip() else None
    return CurrentUser(id=x_user_id, roles=roles, email=email)


def require_roles(*roles: str):
    def _check(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if not user.has_any_role(*roles):
            raise ForbiddenError(f"Requires one of the roles: {', '.join(roles)}")
        return user

    return _check
