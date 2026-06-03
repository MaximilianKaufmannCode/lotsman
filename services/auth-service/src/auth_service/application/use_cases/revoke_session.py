# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""RevokeSession use case — US-14 (admin revokes one specific session)."""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import RevokeSessionCommand
from auth_service.application.ports import EventOutbox, SessionRepository
from auth_service.domain.errors import SessionNotFoundError
from auth_service.domain.events import SessionRevoked


@dataclass(slots=True)
class RevokeSession:
    session_repo: SessionRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: RevokeSessionCommand) -> None:
        session = await self.session_repo.get_by_id(cmd.session_id)
        if session is None or session.user_id != cmd.target_user_id:
            raise SessionNotFoundError("Session not found")

        # Idempotent: already revoked is fine (204)
        if session.revoked_at is not None:
            return

        await self.session_repo.revoke(session.id)
        await self.outbox.publish(
            SessionRevoked(
                actor_id=cmd.actor_id,
                session_id=session.id,
                target_user_id=session.user_id,
            ).as_envelope()
        )
