# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: web-bff settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from web_bff.config import Settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # All four per-service keys are required (F-002: each key must be unique)
    monkeypatch.setenv("INTERNAL_JWT_KEY_AUTH", "a" * 32)
    monkeypatch.setenv("INTERNAL_JWT_KEY_REGISTRY", "b" * 32)
    monkeypatch.setenv("INTERNAL_JWT_KEY_NOTIFICATION", "c" * 32)
    monkeypatch.setenv("INTERNAL_JWT_KEY_AUDIT", "d" * 32)
    # Clear system-control key to test optional behaviour
    monkeypatch.delenv("INTERNAL_JWT_KEY_SYSTEM_CONTROL", raising=False)
    monkeypatch.setenv("AUTH_SVC_URL", "http://auth-svc:8000")
    s = Settings()  # type: ignore[call-arg]
    assert s.service_name == "web-bff"
    assert s.auth_svc_url == "http://auth-svc:8000"
    assert s.internal_jwt_ttl_seconds == 60
    # system-control key is optional; None when not set
    assert s.internal_jwt_key_system_control is None


def test_settings_missing_required_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """All four internal_jwt_key_* fields are required; missing any raises ValidationError."""
    # Remove all four required per-service keys to trigger validation failure
    for key in (
        "INTERNAL_JWT_KEY_AUTH",
        "INTERNAL_JWT_KEY_REGISTRY",
        "INTERNAL_JWT_KEY_NOTIFICATION",
        "INTERNAL_JWT_KEY_AUDIT",
    ):
        monkeypatch.delenv(key, raising=False)
    # Also clear the optional key so it doesn't interfere
    monkeypatch.delenv("INTERNAL_JWT_KEY_SYSTEM_CONTROL", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
