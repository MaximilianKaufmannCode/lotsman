# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for GetChannels — US-16.3 (never returns secrets, decrypt_error path)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.fernet import Fernet


@dataclass
class FakeRow:
    channel: str
    enabled: bool
    config_enc: bytes
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class FakeCredentialRepo:
    def __init__(self, rows: list[FakeRow]) -> None:
        self._rows = rows

    async def get_all(self) -> list[Any]:
        return self._rows

    async def upsert(self, **kwargs: Any) -> None:
        pass

    async def set_enabled(self, **kwargs: Any) -> None:
        pass


@pytest.fixture
def key_a() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def key_b() -> str:
    return Fernet.generate_key().decode()


@pytest.mark.asyncio
async def test_get_channels_returns_ok_status(
    key_a: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: channel is encrypted with correct key → status='ok'."""
    monkeypatch.setenv("CHANNEL_ENC_KEY", key_a)

    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)
    from notification_service.infrastructure.channel_crypto import ChannelCipher

    cipher = ChannelCipher()
    config_enc = cipher.encrypt({"smtp_password": "secret"})

    row = FakeRow(
        channel="email",
        enabled=True,
        config_enc=config_enc,
        created_by=uuid.uuid4(),
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    from notification_service.application.use_cases.get_channels import GetChannels

    use_case = GetChannels(credential_repo=FakeCredentialRepo([row]), cipher=cipher)
    statuses = await use_case.execute()

    assert len(statuses) == 1
    s = statuses[0]
    assert s.channel == "email"
    assert s.configured is True
    assert s.status == "ok"
    # Result must never carry the config dict itself.
    assert not hasattr(s, "config")


@pytest.mark.asyncio
async def test_get_channels_decrypt_error(
    key_a: str, key_b: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-16.3: Wrong key → status='decrypt_error', secret fields not exposed."""
    monkeypatch.setenv("CHANNEL_ENC_KEY", key_a)

    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)
    cipher_a = mod.ChannelCipher()
    config_enc = cipher_a.encrypt({"smtp_password": "secret"})

    # Switch to key_b — wrong key.
    monkeypatch.setenv("CHANNEL_ENC_KEY", key_b)
    importlib.reload(mod)
    cipher_b = mod.ChannelCipher()

    row = FakeRow(
        channel="email",
        enabled=True,
        config_enc=config_enc,
        created_by=uuid.uuid4(),
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    from notification_service.application.use_cases.get_channels import GetChannels

    use_case = GetChannels(credential_repo=FakeCredentialRepo([row]), cipher=cipher_b)
    statuses = await use_case.execute()

    assert statuses[0].status == "decrypt_error"
    assert statuses[0].configured is True
