# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: system-control internal-JWT authentication."""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from system_control.auth import _verify_token

_SECRET = "test-secret-system-control-32-bytes-long!!"
_ACTOR = uuid.uuid4()


def _mint(
    *,
    secret: str = _SECRET,
    aud: str = "system-control",
    iss: str = "web-bff",
    role: str = "super_admin",
    exp_offset: int = 60,
    sub: str | None = None,
) -> str:
    now = int(time.time())
    payload = {
        "iss": iss,
        "aud": aud,
        "sub": sub or str(_ACTOR),
        "role": role,
        "iat": now,
        "nbf": now,
        "exp": now + exp_offset,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def test_valid_token_accepted() -> None:
    token = _mint()
    claims = _verify_token(token, _SECRET)
    assert claims.role == "super_admin"
    assert claims.actor_id == _ACTOR


def test_wrong_audience_rejected() -> None:
    token = _mint(aud="auth-service")
    with pytest.raises(jwt.InvalidTokenError):
        _verify_token(token, _SECRET)


def test_wrong_issuer_rejected() -> None:
    token = _mint(iss="attacker")
    with pytest.raises(jwt.InvalidTokenError):
        _verify_token(token, _SECRET)


def test_wrong_role_rejected() -> None:
    token = _mint(role="admin")
    with pytest.raises(jwt.InvalidTokenError):
        _verify_token(token, _SECRET)


def test_expired_token_rejected() -> None:
    token = _mint(exp_offset=-10)
    with pytest.raises(jwt.InvalidTokenError):
        _verify_token(token, _SECRET)


def test_wrong_secret_rejected() -> None:
    token = _mint()
    with pytest.raises(jwt.InvalidTokenError):
        _verify_token(token, "wrong-secret-that-is-at-least-32-bytes!!")
