# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Internal JWT issue and verify for Лоцман inter-service calls.

Per ADR-0002 §E and ADR-0003 §10 (binding):
- Algorithm: HS256 (per-service keys; mTLS deferred to ADR-0005)
- Audience-bound: a JWT minted for 'registry-service' is NOT valid at 'notification-service'
- TTL: 60 seconds by default (not refreshed)
- Issuer: 'web-bff' (only the BFF mints internal JWTs for downstream calls)
- nbf claim is set on issue (= iat); leeway=2s on verify (ADR-0003 §10 R-5d / F-004)
- replay_check callback supported for jti replay protection (ADR-0003 §10 R-5c / F-003)

Usage — in web-bff when fanning out::

    token = issue_internal_jwt(
        secret=settings.internal_jwt_key_auth,
        actor_id=current_user.id,
        role=current_user.role,
        audience="auth-service",
    )
    headers = {"X-Internal-Token": token, "X-Request-Id": request_id}

Usage — in backend services validating inbound calls::

    claims = await verify_internal_jwt(
        secret=settings.internal_jwt_key_auth,
        token=request.headers["X-Internal-Token"],
        expected_audience="auth-service",
        replay_check=redis_replay_check,
    )
    actor_id = claims.actor_id
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

# ---------------------------------------------------------------------------
# Claims dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InternalJWTClaims:
    """Decoded and validated claims from an internal JWT."""

    actor_id: uuid.UUID
    """Subject — the acting user or system actor UUID."""

    role: str
    """RBAC role: admin | editor | viewer | system."""

    audience: str
    """The target service this token was minted for."""

    request_id: str | None
    """Propagated trace id (may be None for scheduler-initiated calls)."""

    issued_at: datetime
    """When the token was minted."""

    expires_at: datetime
    """When the token expires (iat + ttl_seconds)."""

    jti: str
    """Unique token identifier; cached by each service for replay detection."""


# ---------------------------------------------------------------------------
# Issue
# ---------------------------------------------------------------------------

_ISSUER = "web-bff"
_ALGORITHM = "HS256"


def issue_internal_jwt(
    secret: str,
    *,
    actor_id: uuid.UUID,
    role: str,
    audience: str,
    request_id: str | None = None,
    ttl_seconds: int = 60,
) -> str:
    """Mint a short-lived internal JWT addressed to a specific downstream service.

    Per ADR-0003 §10 R-5d: adds ``nbf`` claim equal to ``iat``.

    Args:
        secret: Per-target HS256 key (``INTERNAL_JWT_KEY_<SVC>`` env var).
        actor_id: UUID of the acting user or system actor.
        role: RBAC role string passed through to the backend service.
        audience: Target service name, e.g. ``'registry-service'``.
        request_id: Optional trace id for end-to-end correlation.
        ttl_seconds: Token lifetime in seconds (default 60).

    Returns:
        Compact serialised JWT string.
    """
    now = datetime.now(tz=UTC)
    iat = int(now.timestamp())
    exp = iat + ttl_seconds
    jti = str(uuid.uuid4())

    payload: dict[str, object] = {
        "iss": _ISSUER,
        "aud": audience,
        "sub": str(actor_id),
        "role": role,
        "iat": iat,
        "nbf": iat,  # R-5d / F-004
        "exp": exp,
        "jti": jti,
    }
    if request_id is not None:
        payload["request_id"] = request_id

    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

#: Type for the replay-check callback injected by each service's current_actor dep.
#: Contract: await replay_check(audience, jti, ttl_remaining_seconds)
#:   -> True  if first sight (caller should cache the jti)
#:   -> False if already seen (replay)
ReplayCheck = Callable[[str, str, int], Awaitable[bool]]


async def verify_internal_jwt(
    secret: str,
    token: str,
    *,
    expected_audience: str,
    replay_check: ReplayCheck | None = None,
) -> InternalJWTClaims:
    """Verify an internal JWT and return typed claims.

    Per ADR-0003 §10:
    - leeway=2 seconds tolerated (R-5d / F-004)
    - nbf required in required-claims list
    - dataclass construction wrapped to prevent claim-shape leaks (R-5e)
    - optional replay_check for jti dedup (R-5c / F-003)

    Raises:
        jwt.InvalidTokenError: If the token is invalid, expired, audience
            mismatches, replay detected, or claims are malformed.
            Callers should catch this and return HTTP 401.

    Args:
        secret: Per-target HS256 key (``INTERNAL_JWT_KEY_<SVC>`` env var).
        token: The compact JWT string from the ``X-Internal-Token`` header.
        expected_audience: The audience this service expects, e.g. ``'auth-service'``.
        replay_check: Optional async callable for jti replay protection.
            If provided and returns False, raises InvalidTokenError("replay detected").

    Returns:
        :class:`InternalJWTClaims` with all decoded fields.
    """
    decoded: dict[str, object] = jwt.decode(
        token,
        secret,
        algorithms=[_ALGORITHM],
        audience=expected_audience,
        issuer=_ISSUER,
        leeway=2,  # R-5d / F-004: tolerate 2s clock skew
        options={"require": ["exp", "iat", "nbf", "sub", "aud", "iss", "jti", "role"]},
    )

    try:
        now_ts = int(datetime.now(tz=UTC).timestamp())
        exp_ts = int(str(decoded["exp"]))
        ttl_remaining = max(0, exp_ts - now_ts)

        claims = InternalJWTClaims(
            actor_id=uuid.UUID(str(decoded["sub"])),
            role=str(decoded["role"]),
            # PyJWT has already validated aud == expected_audience; use expected_audience directly.
            audience=expected_audience,
            request_id=str(decoded["request_id"]) if "request_id" in decoded else None,
            issued_at=datetime.fromtimestamp(int(str(decoded["iat"])), tz=UTC),
            expires_at=datetime.fromtimestamp(exp_ts, tz=UTC),
            jti=str(decoded["jti"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise jwt.InvalidTokenError("malformed claim shape") from exc

    # R-5c / F-003: jti replay protection
    if replay_check is not None:
        is_first_sight = await replay_check(expected_audience, claims.jti, ttl_remaining)
        if not is_first_sight:
            raise jwt.InvalidTokenError("replay detected")

    return claims
