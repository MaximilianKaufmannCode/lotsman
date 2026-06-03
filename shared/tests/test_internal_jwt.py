# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for lotsman_shared.internal_jwt.

Covers:
- Happy path: issue → verify same audience
- Wrong audience: raises jwt.InvalidAudienceError
- Expired token: raises jwt.ExpiredSignatureError
- Missing required claims: raises jwt.MissingRequiredClaimError
- request_id propagation
- System actor sub
- nbf claim present (ADR-0003 §10 R-5d)
- leeway=2s tolerated (R-5d)
- replay rejection via replay_check callback (R-5c)
- malformed claim shape → InvalidTokenError (R-5e)
- audience mismatch
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import jwt
import pytest
from freezegun import freeze_time

from lotsman_shared.internal_jwt import InternalJWTClaims, issue_internal_jwt, verify_internal_jwt

_SECRET = "test-secret-do-not-use-in-production-must-be-32chars"
_AUDIENCE = "registry-service"


def _issue(
    *,
    actor_id: uuid.UUID | None = None,
    role: str = "editor",
    audience: str = _AUDIENCE,
    request_id: str | None = None,
    ttl_seconds: int = 60,
    secret: str = _SECRET,
) -> str:
    return issue_internal_jwt(
        secret,
        actor_id=actor_id or uuid.uuid4(),
        role=role,
        audience=audience,
        request_id=request_id,
        ttl_seconds=ttl_seconds,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_returns_claims() -> None:
    actor_id = uuid.uuid4()
    token = issue_internal_jwt(
        _SECRET,
        actor_id=actor_id,
        role="admin",
        audience=_AUDIENCE,
        request_id="req-001",
    )
    claims = await verify_internal_jwt(_SECRET, token, expected_audience=_AUDIENCE)

    assert isinstance(claims, InternalJWTClaims)
    assert claims.actor_id == actor_id
    assert claims.role == "admin"
    assert claims.audience == _AUDIENCE
    assert claims.request_id == "req-001"
    assert isinstance(claims.issued_at, datetime)
    assert isinstance(claims.expires_at, datetime)
    assert claims.issued_at.tzinfo is UTC or claims.issued_at.tzinfo is not None
    assert isinstance(claims.jti, str)


@pytest.mark.asyncio
async def test_happy_path_no_request_id() -> None:
    token = _issue(request_id=None)
    claims = await verify_internal_jwt(_SECRET, token, expected_audience=_AUDIENCE)
    assert claims.request_id is None


@pytest.mark.asyncio
async def test_system_actor_sub() -> None:
    from lotsman_shared.actors import ACTOR_OUTBOX_DISPATCHER

    token = issue_internal_jwt(
        _SECRET,
        actor_id=ACTOR_OUTBOX_DISPATCHER,
        role="system",
        audience=_AUDIENCE,
    )
    claims = await verify_internal_jwt(_SECRET, token, expected_audience=_AUDIENCE)
    assert claims.actor_id == ACTOR_OUTBOX_DISPATCHER
    assert claims.role == "system"


# ---------------------------------------------------------------------------
# nbf claim present (R-5d / ADR-0003 §10)
# ---------------------------------------------------------------------------


def test_nbf_present_in_issued_token() -> None:
    """issued token must carry nbf claim equal to iat."""
    token = _issue()
    # Decode without verification to inspect raw payload
    decoded = jwt.decode(token, options={"verify_signature": False}, algorithms=["HS256"])
    assert "nbf" in decoded
    assert decoded["nbf"] == decoded["iat"]


# ---------------------------------------------------------------------------
# leeway tolerated (R-5d / F-004)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leeway_within_2s_tolerated() -> None:
    """A token issued 1s in the future (clock skew) should be accepted within leeway=2s."""
    with freeze_time("2026-05-06 10:00:01"):
        token = _issue(ttl_seconds=60)

    # Verify at the exact issue time (1s before nbf), still within leeway=2
    with freeze_time("2026-05-06 10:00:00"):
        claims = await verify_internal_jwt(_SECRET, token, expected_audience=_AUDIENCE)
    assert claims.actor_id is not None


# ---------------------------------------------------------------------------
# Wrong audience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_audience_raises() -> None:
    token = _issue(audience="registry-service")
    with pytest.raises(jwt.InvalidAudienceError):
        await verify_internal_jwt(_SECRET, token, expected_audience="notification-service")


@pytest.mark.asyncio
async def test_wrong_audience_different_case_raises() -> None:
    token = _issue(audience="Registry-Service")
    with pytest.raises(jwt.InvalidAudienceError):
        await verify_internal_jwt(_SECRET, token, expected_audience="registry-service")


# ---------------------------------------------------------------------------
# Expired token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_token_raises() -> None:
    with freeze_time("2026-05-06 10:00:00"):
        token = _issue(ttl_seconds=30)

    # Advance time past expiry (30s + leeway=2)
    with freeze_time("2026-05-06 10:01:00"), pytest.raises(jwt.ExpiredSignatureError):
        await verify_internal_jwt(_SECRET, token, expected_audience=_AUDIENCE)


@pytest.mark.asyncio
async def test_token_valid_within_ttl() -> None:
    with freeze_time("2026-05-06 10:00:00"):
        token = _issue(ttl_seconds=60)

    # Still within the 60s window
    with freeze_time("2026-05-06 10:00:59"):
        claims = await verify_internal_jwt(_SECRET, token, expected_audience=_AUDIENCE)
    assert claims.actor_id is not None


# ---------------------------------------------------------------------------
# Wrong secret
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_secret_raises() -> None:
    token = _issue(secret=_SECRET)
    with pytest.raises(jwt.InvalidSignatureError):
        await verify_internal_jwt("wrong-secret", token, expected_audience=_AUDIENCE)


# ---------------------------------------------------------------------------
# Tampered token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tampered_token_raises() -> None:
    token = _issue()
    parts = token.split(".")
    # Corrupt the payload
    tampered = parts[0] + "." + "AAAAAAAAAA" + "." + parts[2]
    with pytest.raises(jwt.DecodeError):
        await verify_internal_jwt(_SECRET, tampered, expected_audience=_AUDIENCE)


# ---------------------------------------------------------------------------
# Replay rejection (R-5c / F-003)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_rejection_when_replay_check_returns_false() -> None:
    """verify_internal_jwt raises InvalidTokenError when replay_check returns False."""
    token = _issue()

    # First call accepted (True)
    first_call = AsyncMock(return_value=True)
    claims = await verify_internal_jwt(
        _SECRET, token, expected_audience=_AUDIENCE, replay_check=first_call
    )
    assert claims.jti is not None
    first_call.assert_awaited_once()

    # Second call: same jti → replay_check returns False
    replay_call = AsyncMock(return_value=False)
    with pytest.raises(jwt.InvalidTokenError, match="replay detected"):
        await verify_internal_jwt(
            _SECRET, token, expected_audience=_AUDIENCE, replay_check=replay_call
        )
    replay_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_replay_check_passes_without_redis() -> None:
    """Without replay_check, verification succeeds (backward compat for tests)."""
    token = _issue()
    claims = await verify_internal_jwt(_SECRET, token, expected_audience=_AUDIENCE)
    assert claims.jti is not None


# ---------------------------------------------------------------------------
# Malformed claim shape → InvalidTokenError (R-5e)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_sub_raises_invalid_token_error() -> None:
    """A token whose sub is not a valid UUID raises InvalidTokenError, not ValueError."""
    import base64
    import json

    # Manually craft a JWT with invalid sub
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=")
    now = int(datetime.now(tz=UTC).timestamp())
    payload_data = {
        "iss": "web-bff",
        "aud": _AUDIENCE,
        "sub": "not-a-uuid",
        "role": "editor",
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=")

    import hashlib
    import hmac

    message = header + b"." + payload
    sig = hmac.new(_SECRET.encode(), message, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=")
    bad_token = (message + b"." + sig_b64).decode()

    with pytest.raises(jwt.InvalidTokenError):
        await verify_internal_jwt(_SECRET, bad_token, expected_audience=_AUDIENCE)
