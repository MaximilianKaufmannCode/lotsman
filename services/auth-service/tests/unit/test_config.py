# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: auth-service settings load from environment."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auth_service.config import Settings

_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://auth_app:pw@db/lotsman",
    "INTERNAL_JWT_KEY_AUTH": "a" * 32,
    "TOTP_ENC_KEY": "dGVzdC10b3RwLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcysK",  # valid Fernet-ish b64
    "REDIS_URL": "redis://redis:6379/0",
}


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)

    s = Settings()  # type: ignore[call-arg]
    assert s.database_url == "postgresql+asyncpg://auth_app:pw@db/lotsman"
    assert s.internal_jwt_key_auth == "a" * 32
    assert s.redis_url == "redis://redis:6379/0"
    assert s.service_name == "auth-service"


def test_settings_default_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    s = Settings()  # type: ignore[call-arg]
    assert s.service_name == "auth-service"


def test_settings_missing_database_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_missing_jwt_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("INTERNAL_JWT_KEY_AUTH", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_missing_totp_enc_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TOTP_ENC_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_short_internal_jwt_key_fails_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """INTERNAL_JWT_KEY_AUTH shorter than 32 chars must fail startup (F-001 / ADR-0003 §10 R-5b).

    Closes US-25 edge case: 'INTERNAL_JWT_SECRET shorter than 32 chars startup rejection'.
    """
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("INTERNAL_JWT_KEY_AUTH", "tooshort")  # 8 chars < 32
    with pytest.raises(ValidationError) as exc_info:
        Settings()  # type: ignore[call-arg]
    # Error message must reference the field or length constraint
    assert "internal_jwt_key_auth" in str(exc_info.value).lower() or "32" in str(exc_info.value)


def test_internal_jwt_key_at_min_length_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """INTERNAL_JWT_KEY_AUTH of exactly 32 chars must be accepted."""
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("INTERNAL_JWT_KEY_AUTH", "a" * 32)
    s = Settings()  # type: ignore[call-arg]
    assert len(s.internal_jwt_key_auth) == 32


# ---------------------------------------------------------------------------
# TTL and cookie-attribute config tests (ADR-0003 §13 amendment 2026-05-12)
# ---------------------------------------------------------------------------


def test_default_token_ttls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defaults: access=900 s (15 min), refresh=43200 s (12 h)."""
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    s = Settings()  # type: ignore[call-arg]
    assert s.access_token_ttl_seconds == 900
    assert s.refresh_token_ttl_seconds == 43200


def test_custom_token_ttls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Custom TTLs are accepted when within the allowed range."""
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "1800")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "86400")
    s = Settings()  # type: ignore[call-arg]
    assert s.access_token_ttl_seconds == 1800
    assert s.refresh_token_ttl_seconds == 86400


def test_access_ttl_below_minimum_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """ACCESS_TOKEN_TTL_SECONDS < 60 must fail validation."""
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "59")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_refresh_ttl_below_minimum_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """REFRESH_TOKEN_TTL_SECONDS < 3600 must fail validation."""
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3599")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_default_cookie_secure_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """REFRESH_COOKIE_SECURE defaults to True (prod-safe)."""
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    s = Settings()  # type: ignore[call-arg]
    assert s.refresh_cookie_secure is True
    assert s.refresh_cookie_samesite == "strict"


def test_dev_cookie_attrs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev env can set Secure=false and SameSite=lax."""
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("REFRESH_COOKIE_SECURE", "false")
    monkeypatch.setenv("REFRESH_COOKIE_SAMESITE", "lax")
    s = Settings()  # type: ignore[call-arg]
    assert s.refresh_cookie_secure is False
    assert s.refresh_cookie_samesite == "lax"
