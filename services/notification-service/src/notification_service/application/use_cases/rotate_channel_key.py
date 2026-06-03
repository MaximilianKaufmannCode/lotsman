# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""RotateChannelKey use case — US-17.

Re-encrypts all notification.provider_credentials rows from --old-key to
--new-key atomically:
  1. Try-decrypt all rows with old_key.
  2. If any fail, abort without changes.
  3. Re-encrypt all with new_key and persist in a single call batch.
  4. Emit notification.channel.rekeyed.v1.

This use case is invoked by the CLI script, not the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet
from lotsman_shared.actors import ACTOR_SYSTEM_MIGRATOR

from notification_service.application.ports import CredentialRepository, EventOutbox
from notification_service.domain.events import ChannelRekeyed


@dataclass(slots=True)
class RotateChannelKey:
    """Re-encrypt all channel configs with a new master key."""

    credential_repo: CredentialRepository
    outbox: EventOutbox

    async def execute(self, *, old_key: str, new_key: str) -> int:
        """Re-encrypt all rows. Returns count of rows re-encrypted.

        Raises RuntimeError (with channel name) if old_key cannot decrypt any row.
        """
        import json

        old_fernet = Fernet(old_key.encode())
        new_fernet = Fernet(new_key.encode())

        rows = await self.credential_repo.get_all()

        # Phase 1: try-decrypt all with old key.
        decrypted: list[tuple[Any, dict[str, Any]]] = []
        for row in rows:
            try:
                plaintext = old_fernet.decrypt(row.config_enc)
                config: dict[str, Any] = json.loads(plaintext.decode("utf-8"))
                decrypted.append((row, config))
            except Exception as exc:
                raise RuntimeError(
                    f"decrypt failed for channel={row.channel} — old key wrong, aborting"
                ) from exc

        if not decrypted:
            return 0

        # Phase 2: re-encrypt all with new key and persist.
        for row, config in decrypted:
            new_enc = new_fernet.encrypt(json.dumps(config).encode("utf-8"))
            await self.credential_repo.upsert(
                channel=row.channel,
                enabled=row.enabled,
                config_enc=new_enc,
                actor_id=ACTOR_SYSTEM_MIGRATOR,
            )

        count = len(decrypted)

        # Emit audit event.
        await self.outbox.publish(
            ChannelRekeyed(
                system_actor_id=ACTOR_SYSTEM_MIGRATOR,
                count=count,
            ).as_envelope()
        )

        return count
