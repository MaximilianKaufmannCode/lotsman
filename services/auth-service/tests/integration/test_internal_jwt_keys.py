# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration tests for per-service internal-JWT key isolation (US-25, F-002, ADR-0003 §10).

Verifies:
1. An internal JWT signed with KEY_AUTH and aud='auth-service' is REJECTED by
   'registry-service' (which holds KEY_REGISTRY, not KEY_AUTH).
2. Same JWT presented to 'auth-service' (correct audience) is ACCEPTED.
3. A token with aud='registry-service' is REJECTED at 'auth-service'.
"""

from __future__ import annotations

import uuid

import jwt as pyjwt
import pytest
from lotsman_shared.internal_jwt import issue_internal_jwt, verify_internal_jwt

_KEY_AUTH = "auth-service-key-32chars-xxxxxxxx"
_KEY_REGISTRY = "registry-service-key-32chars-yyyy"
_ACTOR_ID = uuid.uuid4()

assert len(_KEY_AUTH) >= 32
assert len(_KEY_REGISTRY) >= 32


@pytest.mark.asyncio
async def test_auth_service_jwt_accepted_by_auth_service() -> None:
    """A JWT minted for auth-service verifies correctly at auth-service."""
    token = issue_internal_jwt(
        _KEY_AUTH,
        actor_id=_ACTOR_ID,
        role="admin",
        audience="auth-service",
    )
    claims = await verify_internal_jwt(_KEY_AUTH, token, expected_audience="auth-service")
    assert claims.audience == "auth-service"
    assert claims.actor_id == _ACTOR_ID


@pytest.mark.asyncio
async def test_auth_service_jwt_rejected_by_registry_service() -> None:
    """A JWT minted for auth-service is REJECTED at registry-service (wrong audience, closes F-002)."""
    token = issue_internal_jwt(
        _KEY_AUTH,
        actor_id=_ACTOR_ID,
        role="editor",
        audience="auth-service",
    )
    # registry-service holds KEY_REGISTRY, not KEY_AUTH
    with pytest.raises(pyjwt.InvalidTokenError):
        await verify_internal_jwt(_KEY_REGISTRY, token, expected_audience="registry-service")


@pytest.mark.asyncio
async def test_registry_service_jwt_rejected_at_auth_service() -> None:
    """A JWT minted for registry-service is REJECTED at auth-service."""
    token = issue_internal_jwt(
        _KEY_REGISTRY,
        actor_id=_ACTOR_ID,
        role="editor",
        audience="registry-service",
    )
    with pytest.raises(pyjwt.InvalidTokenError):
        await verify_internal_jwt(_KEY_AUTH, token, expected_audience="auth-service")


@pytest.mark.asyncio
async def test_correct_key_wrong_audience_rejected() -> None:
    """Even with the correct key, wrong aud claim is rejected."""
    token = issue_internal_jwt(
        _KEY_AUTH,
        actor_id=_ACTOR_ID,
        role="editor",
        audience="notification-service",  # wrong audience for auth-service
    )
    with pytest.raises(pyjwt.InvalidAudienceError):
        await verify_internal_jwt(_KEY_AUTH, token, expected_audience="auth-service")


@pytest.mark.asyncio
async def test_algorithm_confusion_alg_none_rejected() -> None:
    """alg=none tokens must be rejected (algorithm pinning, ADR-0003 §7)."""
    import base64
    import json

    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=")
    import time

    now = int(time.time())
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "iss": "web-bff",
                "aud": "auth-service",
                "sub": str(_ACTOR_ID),
                "role": "admin",
                "iat": now,
                "nbf": now,
                "exp": now + 60,
                "jti": str(uuid.uuid4()),
            }
        ).encode()
    ).rstrip(b"=")
    unsigned_token = (header + b"." + payload + b".").decode()

    with pytest.raises(pyjwt.InvalidTokenError):
        await verify_internal_jwt(_KEY_AUTH, unsigned_token, expected_audience="auth-service")
