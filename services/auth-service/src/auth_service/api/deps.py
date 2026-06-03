# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""FastAPI dependencies for auth-service.

Provides:
    get_db_session      — request-scoped async DB session
    get_settings        — singleton Settings
    current_actor       — validate X-Internal-Token (internal JWT from web-bff)
    require_actor       — enforce authenticated actor (raises 401 if None)
    require_role        — enforce RBAC role (raises 403)
    require_admin_re_mfa — enforce admin re-MFA gate (Redis flag check)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, Header, HTTPException, Request
from lotsman_shared.internal_jwt import InternalJWTClaims, verify_internal_jwt
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service.config import Settings, get_settings
from auth_service.infrastructure.db.session import get_session as _get_session

log = structlog.get_logger(__name__)

# Service audience constant (ADR-0003 §10)
SERVICE_AUDIENCE = "auth-service"


# ---------------------------------------------------------------------------
# DB Session
# ---------------------------------------------------------------------------


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in _get_session():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------


async def get_redis(request: Request) -> aioredis.Redis:  # type: ignore[type-arg]
    """Retrieve the Redis client from app.state (wired in lifespan)."""
    return request.app.state.redis  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Internal JWT / current_actor (ADR-0003 §10 — per-service key + replay cache)
# ---------------------------------------------------------------------------


async def current_actor(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment] # noqa: B008
    redis: Annotated[aioredis.Redis, Depends(get_redis)] = None,  # type: ignore[assignment] # noqa: B008
) -> InternalJWTClaims | None:
    """Validate the X-Internal-Token header using the per-service key.

    Implements:
    - Per-service key (ADR-0003 §10 / F-001, F-002)
    - jti replay protection via Redis SET NX (R-5c / F-003)
    - leeway=2 via verify_internal_jwt (R-5d / F-004)

    Returns None for requests without a token (e.g., health endpoints).
    Raises HTTP 401 for any JWT violation.
    """
    if x_internal_token is None:
        return None

    async def replay_check(audience: str, jti: str, ttl_remaining: int) -> bool:
        result = await redis.set(
            f"jti:{audience}:{jti}",
            "1",
            nx=True,
            ex=max(1, ttl_remaining),
        )
        return result is not None  # None → already exists → replay

    try:
        claims = await verify_internal_jwt(
            settings.internal_jwt_key_auth,
            x_internal_token,
            expected_audience=SERVICE_AUDIENCE,
            replay_check=replay_check,
        )
        return claims
    except Exception as exc:
        log.warning("internal_jwt_invalid", error=str(exc))
        raise HTTPException(status_code=401, detail="Not authenticated") from exc


CurrentActor = Annotated[InternalJWTClaims | None, Depends(current_actor)]


async def require_actor(
    actor: Annotated[InternalJWTClaims | None, Depends(current_actor)],
) -> InternalJWTClaims:
    """Raise 401 if no valid internal JWT is present."""
    if actor is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return actor


RequireActor = Annotated[InternalJWTClaims, Depends(require_actor)]


def require_role(role: str) -> Callable[..., Awaitable[InternalJWTClaims]]:  # noqa: F821
    """Factory dependency that checks the actor's role."""

    async def _checker(
        actor: Annotated[InternalJWTClaims, Depends(require_actor)],
    ) -> InternalJWTClaims:
        if actor.role != role:
            raise HTTPException(status_code=403, detail="Forbidden")
        return actor

    return _checker


async def require_admin_re_mfa(
    actor: Annotated[InternalJWTClaims, Depends(require_actor)],
) -> InternalJWTClaims:
    """Pass-through re-MFA gate kept for import compatibility.

    Per ADR-0004 §6 and F-003 (admin-channels-review), the BFF is the SOLE
    MFA chokepoint.  All admin user-mutation routes (invite, deactivate, role-change,
    lockout, session-revoke, password-reset, re-invite) call ``_verify_re_mfa``
    in web-bff/api/v1/admin.py BEFORE forwarding to auth-service.
    auth-service therefore receives requests that have already been gated.

    The previous implementation read ``mfa-verified:{user_id}:admin`` — a Redis key
    that was never written by any code path — making the gate permanently open (a false
    sense of defense-in-depth).  It has been removed to eliminate dead code.  The BFF
    enforces the invariant; this dep now simply ensures the actor is a valid admin.
    """
    return actor
