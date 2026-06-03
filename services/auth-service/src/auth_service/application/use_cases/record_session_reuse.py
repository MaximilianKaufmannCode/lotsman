# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""RecordSessionReuse use case — US-10 (chain-revoke).

Called when reuse is detected. Chain-revokes ALL active sessions for the user
and emits the high-severity audit event.

In practice this logic runs inline inside RefreshTokens to keep it atomic.
This module exists for explicit mapping to US-10 and for test isolation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from auth_service.application.ports import EventOutbox, SessionRepository
from auth_service.domain.events import SessionReuseDetected


@dataclass(slots=True)
class RecordSessionReuse:
    session_repo: SessionRepository
    outbox: EventOutbox

    async def execute(self, *, user_id: uuid.UUID | None) -> None:
        if user_id is not None:
            await self.session_repo.revoke_all_for_user(user_id)
        await self.outbox.publish(SessionReuseDetected(actor_id=user_id).as_envelope())
