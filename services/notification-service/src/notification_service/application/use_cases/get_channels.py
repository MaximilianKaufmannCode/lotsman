# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""GetChannels use case — US-16.3, ADR-0004 §4.

Returns channel list with status but NEVER returns the actual config.
Admin must re-submit credentials via PUT to update.

Prometheus counter channel_decrypt_errors_total{channel=...} is incremented
on decryption failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from prometheus_client import Counter

from notification_service.application.ports import CredentialRepository
from notification_service.infrastructure.channel_crypto import ChannelCipher

_decrypt_errors = Counter(
    "channel_decrypt_errors_total",
    "Total Fernet decryption errors when loading channel configs",
    labelnames=["channel"],
)


@dataclass(frozen=True)
class ChannelStatusDTO:
    channel: str
    enabled: bool
    configured: bool
    updated_at: datetime | None
    status: str  # "ok" | "decrypt_error" | "not_configured"


@dataclass(slots=True)
class GetChannels:
    """Return channel status list without exposing encrypted configs."""

    credential_repo: CredentialRepository
    cipher: ChannelCipher

    async def execute(self) -> list[ChannelStatusDTO]:
        rows = await self.credential_repo.get_all()

        result: list[ChannelStatusDTO] = []
        for row in rows:
            channel = row.channel
            config_enc: bytes = row.config_enc

            if not config_enc:
                status = "not_configured"
            else:
                try:
                    self.cipher.decrypt(config_enc)
                    status = "ok"
                except Exception:
                    status = "decrypt_error"
                    _decrypt_errors.labels(channel=channel).inc()

            result.append(
                ChannelStatusDTO(
                    channel=channel,
                    enabled=row.enabled,
                    configured=bool(config_enc),
                    updated_at=getattr(row, "updated_at", None),
                    status=status,
                )
            )

        return result
