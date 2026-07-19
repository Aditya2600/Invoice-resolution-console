"""Small, explicit request authentication boundary.

There is deliberately no login flow here. Development identity is only available when the
process itself is explicitly marked as development; deployed environments must validate JWTs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Callable

import jwt
from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings


@dataclass(frozen=True)
class Actor:
    subject: str
    display_name: str
    role: str


def validate_auth_configuration() -> None:
    settings = get_settings()
    if settings.auth_mode == "development" and settings.environment != "development":
        raise RuntimeError("AUTH_MODE=development is allowed only when ENVIRONMENT=development.")
    if settings.auth_mode not in {"development", "jwt"}:
        raise RuntimeError("AUTH_MODE must be development or jwt.")
    if settings.auth_mode == "jwt" and not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is required when AUTH_MODE=jwt.")
    if settings.jwt_algorithm.lower() == "none":
        raise RuntimeError("JWT_ALGORITHM=none is not allowed.")
    if settings.auth_mode == "development" and settings.demo_actor_role not in {
        "viewer",
        "operator",
        "reviewer",
        "admin",
    }:
        raise RuntimeError("DEMO_ACTOR_ROLE must be viewer, operator, reviewer, or admin.")


def current_actor(request: Request) -> Actor:
    settings = get_settings()
    if settings.auth_mode == "development":
        # No header can alter this identity; it is visibly a local demo account.
        return Actor(settings.demo_actor_id, settings.demo_actor_name, settings.demo_actor_role)
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer authentication is required.", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience or None,
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    subject = str(claims.get("sub") or "").strip()
    role = str(claims.get("role") or "").strip().lower()
    display_name = str(claims.get("name") or claims.get("preferred_username") or subject).strip()
    if not subject or role not in {"viewer", "operator", "reviewer", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token has no permitted invoice-console role.")
    return Actor(subject, display_name or subject, role)


def require_role(*roles: str) -> Callable:
    def dependency(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
        if actor.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission for this action.",
            )
        return actor

    return dependency


read_access = require_role("viewer", "operator", "reviewer", "admin")
upload_access = require_role("operator", "admin")
review_access = require_role("reviewer", "admin")
admin_access = require_role("admin")
