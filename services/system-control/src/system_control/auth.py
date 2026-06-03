# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Internal-JWT authentication for system-control sidecar.

Every request to /v1/* must carry a valid internal JWT with:
  - aud = "system-control"
  - iss = "web-bff"
  - alg = HS256
  - exp valid (TTL 60 s from mint)
  - sub = a UUID (the acting super_admin user ID)
  - role = "super_admin"

No fallback authentication. No basic-auth. Any deviation → 401.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

import jwt
import structlog
from fastapi import Depends, Header, HTTPException

from system_control.config import Settings, get_settings

log = structlog.get_logger(__name__)

_ALGORITHM = "HS256"
_EXPECTED_AUDIENCE = "system-control"
_EXPECTED_ISSUER = "web-bff"
_EXPECTED_ROLE = "super_admin"


@dataclass(frozen=True, slots=True)
class InternalClaims:
    """Decoded claims from a verified system-control internal JWT."""

    actor_id: uuid.UUID
    role: str
    request_id: str | None
    jti: str


def _verify_token(token: str, secret: str) -> InternalClaims:
    """Decode and validate an internal JWT. Raises jwt.InvalidTokenError on any failure."""
    decoded: dict[str, object] = jwt.decode(
        token,
        secret,
        algorithms=[_ALGORITHM],
        audience=_EXPECTED_AUDIENCE,
        issuer=_EXPECTED_ISSUER,
        leeway=2,
        options={"require": ["exp", "iat", "nbf", "sub", "aud", "iss", "jti", "role"]},
    )

    role = str(decoded.get("role", ""))
    if role != _EXPECTED_ROLE:
        raise jwt.InvalidTokenError(f"role '{role}' is not allowed; expected '{_EXPECTED_ROLE}'")

    return InternalClaims(
        actor_id=uuid.UUID(str(decoded["sub"])),
        role=role,
        request_id=str(decoded["request_id"]) if "request_id" in decoded else None,
        jti=str(decoded["jti"]),
    )


async def require_internal_jwt(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> InternalClaims:
    """FastAPI dependency — validates internal JWT, raises HTTP 401 on any failure.

    This is the single auth gate for all /v1/* endpoints. No fallback.
    """
    if x_internal_token is None:
        log.warning("system_control_missing_token")
        raise HTTPException(status_code=401, detail="Missing internal token")
    try:
        claims = _verify_token(x_internal_token, settings.internal_jwt_key_system_control)
    except jwt.InvalidTokenError as exc:
        log.warning("system_control_invalid_token", error=str(exc))
        raise HTTPException(status_code=401, detail="Invalid internal token") from exc
    except Exception as exc:
        log.warning("system_control_auth_error", error=str(exc))
        raise HTTPException(status_code=401, detail="Invalid internal token") from exc

    log.info(
        "system_control_authenticated",
        actor_id=str(claims.actor_id),
        jti=claims.jti,
    )
    return claims


RequireInternalJWT = Annotated[InternalClaims, Depends(require_internal_jwt)]
