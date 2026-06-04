# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""FastAPI dependencies for web-bff.

web-bff has no database. Dependencies here provide:
  - get_settings: singleton Settings
  - get_auth_client / get_registry_client / etc: downstream HTTP clients
    from app.state (wired in lifespan)
  - current_access_claims: decode + verify the external RS256 Bearer JWT
  - require_admin: fast 403 gate for non-admins (no round-trip)
  - read_refresh_cookie: extract the opaque refresh token from the cookie
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import Cookie, Depends, Header, HTTPException, Request

from web_bff.config import Settings, get_settings
from web_bff.infrastructure.clients.audit_client import AuditClient
from web_bff.infrastructure.clients.auth_client import AuthClient
from web_bff.infrastructure.clients.notification_client import NotificationClient
from web_bff.infrastructure.clients.registry_client import RegistryClient

log = structlog.get_logger(__name__)

AppSettings = Annotated[Settings, Depends(get_settings)]


# ---------------------------------------------------------------------------
# Downstream client accessors
# ---------------------------------------------------------------------------


def get_auth_client(request: Request) -> AuthClient:
    return request.app.state.auth_client  # type: ignore[no-any-return]


def get_registry_client(request: Request) -> RegistryClient:
    return request.app.state.registry_client  # type: ignore[no-any-return]


def get_notification_client(request: Request) -> NotificationClient:
    return request.app.state.notification_client  # type: ignore[no-any-return]


def get_audit_client(request: Request) -> AuditClient:
    return request.app.state.audit_client  # type: ignore[no-any-return]


GetAuthClient = Annotated[AuthClient, Depends(get_auth_client)]
GetNotificationClient = Annotated[NotificationClient, Depends(get_notification_client)]
GetAuditClient = Annotated[AuditClient, Depends(get_audit_client)]
GetRegistryClient = Annotated[RegistryClient, Depends(get_registry_client)]


# ---------------------------------------------------------------------------
# External JWT claims (RS256, from the SPA's Authorization: Bearer header)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """Decoded claims from the external RS256 access JWT (issued by auth-service)."""

    subject: uuid.UUID
    """User UUID (sub claim)."""

    email: str
    role: str
    session_id: str
    """sid claim — links to auth.sessions.id."""

    jti: str


def _verify_access_jwt(token: str, settings: Settings) -> AccessClaims:
    """Decode and validate the external RS256 access JWT.

    ADR-0003 §7: alg pinned to RS256, audience 'lotsman-spa', issuer 'lotsman-auth'.
    Required claims: exp, iat, nbf, sub, aud, iss, jti, sid, role.

    Fail-closed: signature verification with the mounted RS256 public key is
    mandatory by default. The unverified structural decode is permitted ONLY
    when jwt_allow_unverified=True (Settings also rejects a missing key at
    startup unless that opt-in is set, per config.py validator).
    """
    import jwt  # type: ignore[import]

    # Try RS256 with the public key first.
    public_key_path = getattr(settings, "jwt_public_key_path", None)
    if public_key_path:
        try:
            with open(public_key_path, "rb") as f:
                public_key = f.read()
            decoded: dict = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience="lotsman-spa",
                issuer="lotsman-auth",
                leeway=2,
                options={
                    "require": ["exp", "iat", "nbf", "sub", "aud", "iss", "jti", "sid", "role"]
                },
            )
        except Exception as exc:
            raise jwt.InvalidTokenError(str(exc)) from exc
    else:
        # No public key configured. Permit the unverified decode ONLY when the
        # explicit dev opt-in is set; otherwise fail-closed (Settings also rejects
        # this at startup, so in practice this guard is defence-in-depth).
        if not getattr(settings, "jwt_allow_unverified", False):
            raise jwt.InvalidTokenError(
                "JWT public key not configured; refusing to accept an unverified "
                "token (set JWT_ALLOW_UNVERIFIED=true for local development)."
            )
        decoded = jwt.decode(
            token,
            # nosemgrep  (dev-only, guarded by jwt_allow_unverified + fail-closed Settings)
            options={"verify_signature": False},  # nosemgrep
            algorithms=["RS256"],
        )

    return AccessClaims(
        subject=uuid.UUID(str(decoded["sub"])),
        email=str(decoded.get("email", "")),
        role=str(decoded.get("role", "viewer")),
        session_id=str(decoded.get("sid", "")),
        jti=str(decoded.get("jti", "")),
    )


async def current_access_claims(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment] # noqa: B008
) -> AccessClaims | None:
    """Extract and verify Bearer token from Authorization header.

    Returns None for requests without a token.
    Raises HTTP 401 for invalid tokens.
    """
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    try:
        return _verify_access_jwt(token, settings)
    except Exception as exc:
        log.warning("access_jwt_invalid", error=str(exc))
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc


async def require_access_claims(
    claims: Annotated[AccessClaims | None, Depends(current_access_claims)],
) -> AccessClaims:
    """Raise 401 if no valid Bearer token is present."""
    if claims is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return claims


RequireAccessClaims = Annotated[AccessClaims, Depends(require_access_claims)]


async def require_admin(
    claims: RequireAccessClaims,
) -> AccessClaims:
    """Gate on admin role — fast 403, no round-trip to auth-service."""
    if claims.role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return claims


RequireAdmin = Annotated[AccessClaims, Depends(require_admin)]


# ---------------------------------------------------------------------------
# Refresh cookie reader
# ---------------------------------------------------------------------------


def read_refresh_cookie(
    refresh: Annotated[str | None, Cookie(alias="refresh")] = None,
) -> str | None:
    """Extract the opaque refresh token from the HttpOnly cookie."""
    return refresh


RefreshCookie = Annotated[str | None, Depends(read_refresh_cookie)]


# ---------------------------------------------------------------------------
# Re-MFA token validator
# ---------------------------------------------------------------------------


def get_request_id(request: Request) -> str | None:
    """Extract X-Request-Id header for downstream propagation."""
    return request.headers.get("X-Request-Id")
