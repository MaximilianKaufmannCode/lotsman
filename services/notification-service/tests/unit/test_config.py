# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: notification-service settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from notification_service.config import Settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://notification_app:pw@db/lotsman")
    monkeypatch.setenv("INTERNAL_JWT_KEY_NOTIFICATION", "0" * 32)
    s = Settings()  # type: ignore[call-arg]
    assert s.service_name == "notification-service"
    assert s.smtp_host == "localhost"


def test_settings_missing_database_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("INTERNAL_JWT_KEY_NOTIFICATION", "0" * 32)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
