# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""DisableChannel use case — US-6.

Sets enabled=false for the row, leaves config_enc untouched.
Pre-check: if this is the only enabled channel AND there are pending
invite OTPs in Redis → raises PendingInvitationsError (409).

Emits notification.channel.disabled.v1.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from lotsman_shared.actors import ACTOR_SYSTEM_MIGRATOR

from notification_service.application.ports import (
    CredentialRepository,
    EventOutbox,
    RedisInviteStore,
)
from notification_service.domain.channels import Channel
from notification_service.domain.errors import PendingInvitationsError
from notification_service.domain.events import ChannelChanged, ChannelDisabled


@dataclass(slots=True)
class DisableChannel:
    """Disable a channel without losing its encrypted config."""

    credential_repo: CredentialRepository
    invite_store: RedisInviteStore
    outbox: EventOutbox

    async def execute(self, *, actor_id: uuid.UUID, channel: Channel) -> None:
        # Fetch all rows to determine if this is the only enabled channel.
        all_rows = await self.credential_repo.get_all()
        enabled_channels = [r.channel for r in all_rows if r.enabled]

        is_only_enabled = enabled_channels == [channel] or (
            len(enabled_channels) == 1 and channel in enabled_channels
        )

        if is_only_enabled:
            has_pending = await self.invite_store.has_pending_invites()
            if has_pending:
                raise PendingInvitationsError()

        await self.credential_repo.set_enabled(channel=channel, enabled=False)

        await self.outbox.publish(
            ChannelDisabled(actor_id=actor_id, channel=channel).as_envelope()
        )

        # Internal hot-reload signal.
        await self.outbox.publish(
            ChannelChanged(
                system_actor_id=ACTOR_SYSTEM_MIGRATOR,
                channel=channel,
            ).as_envelope()
        )
