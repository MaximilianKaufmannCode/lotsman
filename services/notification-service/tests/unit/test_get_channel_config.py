# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for GetChannelConfig use case."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def enc_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CHANNEL_ENC_KEY", key)
    return key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cipher(key: str) -> Any:
    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)
    return mod.ChannelCipher()


def _make_row(channel: str, config: dict[str, Any], cipher: Any) -> FakeRow:
    return FakeRow(
        channel=channel,
        enabled=True,
        config_enc=cipher.encrypt(config),
        created_by=uuid.uuid4(),
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_email_config_happy_path(enc_key: str) -> None:
    """Happy path: secrets masked, non-secret fields returned verbatim."""
    from notification_service.application.use_cases.get_channel_config import GetChannelConfig

    cipher = _make_cipher(enc_key)
    stored = {
        "smtp_host": "mail.example.com",
        "smtp_port": 587,
        "smtp_user": "CORP\\user",
        "smtp_password": "super-secret",
        "from_address": "noreply@example.com",
        "from_name": "Лоцман",
    }
    row = _make_row("email", stored, cipher)
    repo = FakeCredentialRepo([row])
    use_case = GetChannelConfig(credential_repo=repo, cipher=cipher)

    result = await use_case.execute(channel="email")

    # Non-secret fields survive unmolested.
    assert result["smtp_host"] == "mail.example.com"
    assert result["smtp_port"] == 587
    assert result["smtp_user"] == "CORP\\user"
    assert result["from_address"] == "noreply@example.com"
    assert result["from_name"] == "Лоцман"

    # Secret field replaced by the exact placeholder.
    assert result["smtp_password"] == "********"

    # Raw secret never leaks.
    assert "super-secret" not in result.values()


@pytest.mark.asyncio
async def test_get_exchange_calendar_config_secret_masked(enc_key: str) -> None:
    """service_account_password must be masked for exchange_calendar channel."""
    from notification_service.application.use_cases.get_channel_config import GetChannelConfig

    cipher = _make_cipher(enc_key)
    stored = {
        "ews_url": "https://ews.example.com/EWS/Exchange.asmx",
        "service_account_login": "CORP\\svc",
        "service_account_password": "very-secret",
        "target_mailbox": "lotsman@example.com",
        "auth_type": "NTLM",
        "verify_ssl": True,
        "default_notice_days": 14,
    }
    row = _make_row("exchange_calendar", stored, cipher)
    repo = FakeCredentialRepo([row])
    use_case = GetChannelConfig(credential_repo=repo, cipher=cipher)

    result = await use_case.execute(channel="exchange_calendar")

    assert result["service_account_password"] == "********"
    assert result["service_account_login"] == "CORP\\svc"
    assert result["ews_url"] == "https://ews.example.com/EWS/Exchange.asmx"


@pytest.mark.asyncio
async def test_get_channel_config_missing_channel_raises_not_configured(enc_key: str) -> None:
    """ChannelNotConfiguredError raised when no row exists for the channel."""
    from notification_service.application.use_cases.get_channel_config import GetChannelConfig
    from notification_service.domain.errors import ChannelNotConfiguredError

    cipher = _make_cipher(enc_key)
    repo = FakeCredentialRepo([])  # empty — no rows
    use_case = GetChannelConfig(credential_repo=repo, cipher=cipher)

    with pytest.raises(ChannelNotConfiguredError):
        await use_case.execute(channel="email")


@pytest.mark.asyncio
async def test_get_channel_config_decrypt_error_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ChannelDecryptError raised when stored blob cannot be decrypted."""
    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    key_a = Fernet.generate_key().decode()
    monkeypatch.setenv("CHANNEL_ENC_KEY", key_a)
    importlib.reload(mod)
    cipher_a = mod.ChannelCipher()

    # Encrypt with key_a.
    row = _make_row("email", {"smtp_password": "secret"}, cipher_a)

    # Switch to key_b — wrong key → decrypt fails.
    key_b = Fernet.generate_key().decode()
    monkeypatch.setenv("CHANNEL_ENC_KEY", key_b)
    importlib.reload(mod)
    cipher_b = mod.ChannelCipher()

    from notification_service.application.use_cases.get_channel_config import GetChannelConfig
    from notification_service.domain.errors import ChannelDecryptError

    repo = FakeCredentialRepo([row])
    use_case = GetChannelConfig(credential_repo=repo, cipher=cipher_b)

    with pytest.raises(ChannelDecryptError):
        await use_case.execute(channel="email")


@pytest.mark.asyncio
async def test_get_telegram_config_bot_token_masked(enc_key: str) -> None:
    """bot_token is a secret field for telegram channel — must be masked."""
    from notification_service.application.use_cases.get_channel_config import GetChannelConfig

    cipher = _make_cipher(enc_key)
    stored = {
        "bot_token": "123456789:ABCDEFghijklmNOPQRSTuvwxyz1234567890",
        "default_parse_mode": "HTML",
    }
    row = _make_row("telegram", stored, cipher)
    repo = FakeCredentialRepo([row])
    use_case = GetChannelConfig(credential_repo=repo, cipher=cipher)

    result = await use_case.execute(channel="telegram")

    assert result["bot_token"] == "********"
    assert result["default_parse_mode"] == "HTML"
