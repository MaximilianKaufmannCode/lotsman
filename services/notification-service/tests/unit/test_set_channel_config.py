# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for SetChannelConfig — US-2.1, US-2.2, US-3.2, US-4.2, partial-secret."""

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
    created_by: uuid.UUID = uuid.uuid4()
    created_at: datetime = datetime.now(tz=UTC)
    updated_at: datetime = datetime.now(tz=UTC)


class FakeCredentialRepo:
    def __init__(self) -> None:
        self.upserted: list[dict] = []
        self.rows: list[Any] = []

    async def get_all(self) -> list[Any]:
        return self.rows

    async def upsert(
        self,
        *,
        channel: str,
        enabled: bool,
        config_enc: bytes,
        actor_id: uuid.UUID,
    ) -> None:
        self.upserted.append(
            {"channel": channel, "enabled": enabled, "config_enc": config_enc}
        )

    async def set_enabled(self, *, channel: str, enabled: bool) -> None:
        pass


class FakeOutbox:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, envelope: Any) -> None:
        self.events.append(envelope)

    def event_types(self) -> list[str]:
        return [e.type for e in self.events]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def channel_enc_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CHANNEL_ENC_KEY", key)
    return key


@pytest.mark.asyncio
async def test_set_email_config_happy_path(
    channel_enc_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-2.1: Happy path — full email config saved encrypted."""
    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)

    from notification_service.application.use_cases.set_channel_config import SetChannelConfig

    repo = FakeCredentialRepo()
    outbox = FakeOutbox()
    cipher = mod.ChannelCipher()

    use_case = SetChannelConfig(credential_repo=repo, outbox=outbox, cipher=cipher)
    actor_id = uuid.uuid4()
    config = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "user",
        "smtp_password": "secret",
        "from_address": "noreply@example.com",
        "from_name": "Лоцман",
    }

    await use_case.execute(actor_id=actor_id, channel="email", config=config, enabled=True)

    assert len(repo.upserted) == 1
    row = repo.upserted[0]
    assert row["channel"] == "email"
    assert row["enabled"] is True
    # Encrypted bytes must not contain the password.
    assert b"secret" not in row["config_enc"]
    # Must be decryptable.
    decrypted = cipher.decrypt(row["config_enc"])
    assert decrypted["smtp_password"] == "secret"

    # Audit event emitted.
    assert "notification.channel.configured.v1" in outbox.event_types()
    # Hot-reload event emitted.
    assert "notification.channel.changed.v1" in outbox.event_types()

    # Audit payload must redact password.
    configured_event = next(
        e for e in outbox.events if e.type == "notification.channel.configured.v1"
    )
    assert configured_event.payload["config"]["smtp_password"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_set_email_config_invalid_port(
    channel_enc_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-2.2: Validation error — bad smtp_port (99999)."""
    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)

    from notification_service.application.use_cases.set_channel_config import SetChannelConfig
    from notification_service.domain.errors import ChannelValidationError

    repo = FakeCredentialRepo()
    outbox = FakeOutbox()
    cipher = mod.ChannelCipher()
    use_case = SetChannelConfig(credential_repo=repo, outbox=outbox, cipher=cipher)

    config = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 99999,
        "smtp_user": "user",
        "smtp_password": "secret",
        "from_address": "noreply@example.com",
        "from_name": "Лоцман",
    }

    with pytest.raises(ChannelValidationError):
        await use_case.execute(
            actor_id=uuid.uuid4(), channel="email", config=config, enabled=True
        )

    # Nothing persisted on validation failure.
    assert len(repo.upserted) == 0


@pytest.mark.asyncio
async def test_telegram_bot_token_format_error(
    channel_enc_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-3.2: bot_token format validation."""
    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)

    from notification_service.application.use_cases.set_channel_config import SetChannelConfig
    from notification_service.domain.errors import ChannelValidationError

    repo = FakeCredentialRepo()
    outbox = FakeOutbox()
    cipher = mod.ChannelCipher()
    use_case = SetChannelConfig(credential_repo=repo, outbox=outbox, cipher=cipher)

    with pytest.raises(ChannelValidationError) as exc_info:
        await use_case.execute(
            actor_id=uuid.uuid4(),
            channel="telegram",
            config={"bot_token": "invalid-format", "default_parse_mode": "HTML"},
            enabled=True,
        )
    assert "bot_token" in str(exc_info.value).lower() or "Telegram" in str(exc_info.value)


@pytest.mark.asyncio
async def test_dion_https_required(
    channel_enc_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-4.2: api_base must use https://."""
    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)

    from notification_service.application.use_cases.set_channel_config import SetChannelConfig
    from notification_service.domain.errors import ChannelValidationError

    repo = FakeCredentialRepo()
    outbox = FakeOutbox()
    cipher = mod.ChannelCipher()
    use_case = SetChannelConfig(credential_repo=repo, outbox=outbox, cipher=cipher)

    with pytest.raises(ChannelValidationError) as exc_info:
        await use_case.execute(
            actor_id=uuid.uuid4(),
            channel="dion",
            config={
                "api_base": "http://dion.corp/api",
                "api_token": "token123",
                "workspace_id": None,
            },
            enabled=True,
        )
    # F-002: sanitized error must not echo back input values, but must name the field.
    err_msg = str(exc_info.value)
    assert "api_base" in err_msg
    assert "http://dion.corp/api" not in err_msg  # input_value must never appear


@pytest.mark.asyncio
async def test_telegram_token_not_leaked_in_error(
    channel_enc_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-002: ChannelValidationError must NOT echo submitted bot_token in error body."""
    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    importlib.reload(mod)

    from notification_service.application.use_cases.set_channel_config import SetChannelConfig
    from notification_service.domain.errors import ChannelValidationError

    repo = FakeCredentialRepo()
    outbox = FakeOutbox()
    cipher = mod.ChannelCipher()
    use_case = SetChannelConfig(credential_repo=repo, outbox=outbox, cipher=cipher)

    secret_token = "MY_SECRET_BOT_TOKEN_12345"
    with pytest.raises(ChannelValidationError) as exc_info:
        await use_case.execute(
            actor_id=uuid.uuid4(),
            channel="telegram",
            config={"bot_token": secret_token, "default_parse_mode": "HTML"},
            enabled=True,
        )
    err_msg = str(exc_info.value)
    # The field name must appear so the admin knows what to fix.
    assert "bot_token" in err_msg
    # The submitted secret value must NOT appear in the error message.
    assert secret_token not in err_msg, (
        "F-002: submitted secret must not be echoed back in ChannelValidationError"
    )


# ---------------------------------------------------------------------------
# Partial-secret tests (edit-dialog pre-populate UX requirement)
# ---------------------------------------------------------------------------


def _make_cipher_and_key(monkeypatch: pytest.MonkeyPatch) -> Any:
    import importlib

    import notification_service.infrastructure.channel_crypto as mod

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CHANNEL_ENC_KEY", key)
    importlib.reload(mod)
    return mod.ChannelCipher()


_FULL_EMAIL_CONFIG = {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_user": "user",
    "smtp_password": "existing-password",
    "from_address": "noreply@example.com",
    "from_name": "Лоцман",
}


@pytest.mark.asyncio
async def test_partial_secret_empty_string_preserves_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sending smtp_password='' with changed host must preserve the stored password."""
    from notification_service.application.use_cases.set_channel_config import SetChannelConfig

    cipher = _make_cipher_and_key(monkeypatch)
    repo = FakeCredentialRepo()

    # Pre-populate repo with an existing row.
    existing_row = FakeRow(
        channel="email",
        enabled=True,
        config_enc=cipher.encrypt(_FULL_EMAIL_CONFIG),
    )
    repo.rows = [existing_row]

    use_case = SetChannelConfig(credential_repo=repo, outbox=FakeOutbox(), cipher=cipher)

    updated_config = {
        **_FULL_EMAIL_CONFIG,
        "smtp_host": "smtp.newhost.local",
        "smtp_password": "",  # «keep existing»
    }

    await use_case.execute(
        actor_id=uuid.uuid4(), channel="email", config=updated_config, enabled=True
    )

    assert len(repo.upserted) == 1
    saved = cipher.decrypt(repo.upserted[0]["config_enc"])
    assert saved["smtp_host"] == "smtp.newhost.local"
    assert saved["smtp_password"] == "existing-password"


@pytest.mark.asyncio
async def test_partial_secret_placeholder_preserves_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sending smtp_password='********' must preserve the stored password."""
    from notification_service.application.use_cases.set_channel_config import SetChannelConfig

    cipher = _make_cipher_and_key(monkeypatch)
    repo = FakeCredentialRepo()
    repo.rows = [
        FakeRow(
            channel="email",
            enabled=True,
            config_enc=cipher.encrypt(_FULL_EMAIL_CONFIG),
        )
    ]

    use_case = SetChannelConfig(credential_repo=repo, outbox=FakeOutbox(), cipher=cipher)

    updated_config = {
        **_FULL_EMAIL_CONFIG,
        "smtp_password": "********",  # «keep existing» placeholder
    }

    await use_case.execute(
        actor_id=uuid.uuid4(), channel="email", config=updated_config, enabled=True
    )

    saved = cipher.decrypt(repo.upserted[0]["config_enc"])
    assert saved["smtp_password"] == "existing-password"


@pytest.mark.asyncio
async def test_partial_secret_actual_new_value_overwrites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sending a real smtp_password must overwrite the stored one."""
    from notification_service.application.use_cases.set_channel_config import SetChannelConfig

    cipher = _make_cipher_and_key(monkeypatch)
    repo = FakeCredentialRepo()
    repo.rows = [
        FakeRow(
            channel="email",
            enabled=True,
            config_enc=cipher.encrypt(_FULL_EMAIL_CONFIG),
        )
    ]

    use_case = SetChannelConfig(credential_repo=repo, outbox=FakeOutbox(), cipher=cipher)

    await use_case.execute(
        actor_id=uuid.uuid4(),
        channel="email",
        config={**_FULL_EMAIL_CONFIG, "smtp_password": "brand-new-password"},
        enabled=True,
    )

    saved = cipher.decrypt(repo.upserted[0]["config_enc"])
    assert saved["smtp_password"] == "brand-new-password"


@pytest.mark.asyncio
async def test_partial_secret_first_time_empty_raises_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-time config with empty smtp_password must raise ChannelValidationError (SECRET_REQUIRED)."""
    from notification_service.application.use_cases.set_channel_config import SetChannelConfig
    from notification_service.domain.errors import ChannelValidationError

    cipher = _make_cipher_and_key(monkeypatch)
    repo = FakeCredentialRepo()  # rows is empty → no existing config

    use_case = SetChannelConfig(credential_repo=repo, outbox=FakeOutbox(), cipher=cipher)

    with pytest.raises(ChannelValidationError) as exc_info:
        await use_case.execute(
            actor_id=uuid.uuid4(),
            channel="email",
            config={
                **_FULL_EMAIL_CONFIG,
                "smtp_password": "",  # empty on first save → must fail
            },
            enabled=True,
        )

    assert "SECRET_REQUIRED" in str(exc_info.value)
    assert "smtp_password" in str(exc_info.value)
    # Nothing should have been persisted.
    assert len(repo.upserted) == 0


@pytest.mark.asyncio
async def test_partial_secret_first_time_placeholder_raises_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-time config with '********' placeholder must also raise SECRET_REQUIRED."""
    from notification_service.application.use_cases.set_channel_config import SetChannelConfig
    from notification_service.domain.errors import ChannelValidationError

    cipher = _make_cipher_and_key(monkeypatch)
    repo = FakeCredentialRepo()

    use_case = SetChannelConfig(credential_repo=repo, outbox=FakeOutbox(), cipher=cipher)

    with pytest.raises(ChannelValidationError) as exc_info:
        await use_case.execute(
            actor_id=uuid.uuid4(),
            channel="email",
            config={**_FULL_EMAIL_CONFIG, "smtp_password": "********"},
            enabled=True,
        )

    assert "SECRET_REQUIRED" in str(exc_info.value)
    assert len(repo.upserted) == 0
