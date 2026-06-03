# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: registry-service settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from registry_service.config import Settings

_KEY_32 = "0" * 32  # 32-byte placeholder satisfying min_length


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://registry_app:pw@db/lotsman")
    monkeypatch.setenv("INTERNAL_JWT_KEY_REGISTRY", _KEY_32)
    monkeypatch.setenv("INTERNAL_JWT_KEY_AUDIT", _KEY_32)
    s = Settings()  # type: ignore[call-arg]
    assert s.database_url == "postgresql+asyncpg://registry_app:pw@db/lotsman"
    assert s.service_name == "registry-service"


def test_settings_missing_database_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("INTERNAL_JWT_KEY_REGISTRY", _KEY_32)
    monkeypatch.setenv("INTERNAL_JWT_KEY_AUDIT", _KEY_32)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_short_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """F-001: HS256 key must be at least 32 bytes."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x")
    monkeypatch.setenv("INTERNAL_JWT_KEY_REGISTRY", "too-short")
    monkeypatch.setenv("INTERNAL_JWT_KEY_AUDIT", _KEY_32)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
