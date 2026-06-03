# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""GetChannelConfig use case — returns decrypted config with secrets masked.

Used by GET /admin/channels/{channel}/config so the edit dialog can
pre-populate non-secret fields. Secret fields are replaced by the literal
string "********" which the frontend treats as «keep existing value».

ADR-0004 §4 — no raw secrets ever leave the service boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from notification_service.application.ports import CredentialRepository
from notification_service.domain.channels import SECRET_FIELDS, Channel
from notification_service.domain.errors import ChannelDecryptError, ChannelNotConfiguredError
from notification_service.infrastructure.channel_crypto import ChannelCipher

_SECRET_PLACEHOLDER = "********"


@dataclass(slots=True)
class GetChannelConfig:
    """Load, decrypt, and mask secrets from the stored channel config.

    Returns a plain dict with all secret fields replaced by ``"********"``.
    Callers must NOT forward the raw decrypted dict anywhere.

    Raises:
        ChannelNotConfiguredError: No row for this channel in the DB.
        ChannelDecryptError: Fernet decryption failed (key rotation issue).
    """

    credential_repo: CredentialRepository
    cipher: ChannelCipher

    async def execute(self, *, channel: Channel) -> dict[str, Any]:
        rows = await self.credential_repo.get_all()
        row = next((r for r in rows if r.channel == channel), None)

        if row is None or not row.config_enc:
            raise ChannelNotConfiguredError()

        try:
            config = self.cipher.decrypt(row.config_enc)
        except Exception as exc:
            raise ChannelDecryptError() from exc

        secret_fields = SECRET_FIELDS.get(channel, set())
        masked = {
            k: _SECRET_PLACEHOLDER if k in secret_fields else v
            for k, v in config.items()
        }
        return masked
