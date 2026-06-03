# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""SetChannelConfig use case — US-2, US-3, US-4, US-16.

Validates the per-channel config model, encrypts with ChannelCipher,
UPSERTs into notification.provider_credentials, and emits two audit events
in the same transaction:
  - notification.channel.configured.v1  (audit, actor-attributed)
  - notification.channel.changed.v1     (internal hot-reload trigger)

Re-MFA is enforced at the BFF layer; this use case trusts the caller.

Partial-secret support (UX requirement — edit dialog pre-populate):
  Secret fields that arrive as empty string (""), None, or the literal
  placeholder "********" are treated as «keep existing value» and copied
  from the currently stored config before re-validation.
  If no existing row exists and a secret field is empty, a 422 is raised
  with code SECRET_REQUIRED.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from lotsman_shared.actors import ACTOR_SYSTEM_MIGRATOR
from pydantic import ValidationError

from notification_service.application.ports import CredentialRepository, EventOutbox
from notification_service.domain.channels import (
    SECRET_FIELDS,
    Channel,
    DionConfig,
    EmailConfig,
    ExchangeCalendarConfig,
    IcsFeedConfig,
    TelegramConfig,
    redact_secrets,
)
from notification_service.domain.errors import ChannelValidationError
from notification_service.domain.events import ChannelChanged, ChannelConfigured
from notification_service.infrastructure.channel_crypto import ChannelCipher

_CONFIG_MODELS: dict[str, type] = {
    "email": EmailConfig,
    "telegram": TelegramConfig,
    "dion": DionConfig,
    "exchange_calendar": ExchangeCalendarConfig,
    "ics_feed": IcsFeedConfig,
}

# Values the caller may send to signal «do not change the existing secret».
_SECRET_KEEP_SENTINELS: frozenset[str] = frozenset({"", "********"})


def _is_keep_sentinel(value: Any) -> bool:
    """Return True if the value signals «keep existing secret»."""
    return value is None or (isinstance(value, str) and value in _SECRET_KEEP_SENTINELS)


@dataclass(slots=True)
class SetChannelConfig:
    """Configure (create or replace) a notification channel credential.

    Secret fields sent as ``""``, ``None``, or ``"********"`` are preserved
    from the existing stored config.  If no existing row exists, these fields
    are required and a ``ChannelValidationError`` with code ``SECRET_REQUIRED``
    is raised.
    """

    credential_repo: CredentialRepository
    outbox: EventOutbox
    cipher: ChannelCipher

    async def execute(
        self,
        *,
        actor_id: uuid.UUID,
        channel: Channel,
        config: dict[str, Any],
        enabled: bool,
    ) -> None:
        secret_fields = SECRET_FIELDS.get(channel, set())

        # Determine which secret fields the caller wants to «keep».
        keep_fields = {
            field
            for field in secret_fields
            if _is_keep_sentinel(config.get(field))
        }

        if keep_fields:
            # Load existing config to fill in kept secrets.
            existing_config = await self._load_existing_config(channel)

            if existing_config is None:
                # First-time save — kept secrets cannot be inferred.
                missing = sorted(keep_fields)
                raise ChannelValidationError(
                    ", ".join(
                        f"{field}: SECRET_REQUIRED"
                        for field in missing
                    )
                )

            # Fill kept secrets from the stored config.
            config = {**config}
            for field in keep_fields:
                if field in existing_config:
                    config[field] = existing_config[field]

        # Validate config against per-channel Pydantic model.
        # F-002: sanitize the ValidationError — emit only loc+type, never input_value.
        model_cls = _CONFIG_MODELS[channel]
        try:
            model_cls(**config)
        except ValidationError as exc:
            sanitized_errors = ", ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}"
                for e in exc.errors()
            )
            raise ChannelValidationError(sanitized_errors) from exc

        # Encrypt config blob.
        config_enc = self.cipher.encrypt(config)

        # Persist (UPSERT).
        await self.credential_repo.upsert(
            channel=channel,
            enabled=enabled,
            config_enc=config_enc,
            actor_id=actor_id,
        )

        # Audit event — secret fields redacted.
        redacted = redact_secrets(channel, config)
        await self.outbox.publish(
            ChannelConfigured(
                actor_id=actor_id,
                channel=channel,
                enabled=enabled,
                redacted_config=redacted,
            ).as_envelope()
        )

        # Internal hot-reload signal (system actor, no human attribution needed).
        await self.outbox.publish(
            ChannelChanged(
                system_actor_id=ACTOR_SYSTEM_MIGRATOR,
                channel=channel,
            ).as_envelope()
        )

    async def _load_existing_config(self, channel: Channel) -> dict[str, Any] | None:
        """Load and decrypt the current config row for *channel*, or return None."""
        rows = await self.credential_repo.get_all()
        row = next((r for r in rows if r.channel == channel), None)
        if row is None or not row.config_enc:
            return None
        try:
            return self.cipher.decrypt(row.config_enc)
        except Exception:
            # Decrypt failure on existing row — treat as «no existing config»
            # so the caller gets SECRET_REQUIRED rather than a decrypt error.
            return None
