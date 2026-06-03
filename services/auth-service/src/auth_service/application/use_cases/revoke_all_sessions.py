# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""RevokeAllSessions use case — US-15 (admin revokes all sessions of a user)."""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import RevokeAllSessionsCommand, RevokeAllSessionsDTO
from auth_service.application.ports import EventOutbox, SessionRepository, UserRepository
from auth_service.domain.errors import UserNotFoundError
from auth_service.domain.events import SessionRevokedAll


@dataclass(slots=True)
class RevokeAllSessions:
    user_repo: UserRepository
    session_repo: SessionRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: RevokeAllSessionsCommand) -> RevokeAllSessionsDTO:
        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()

        revoked_count = await self.session_repo.revoke_all_for_user(cmd.target_user_id)

        if revoked_count > 0:
            await self.outbox.publish(
                SessionRevokedAll(
                    actor_id=cmd.actor_id,
                    target_user_id=cmd.target_user_id,
                    revoked_count=revoked_count,
                ).as_envelope()
            )

        return RevokeAllSessionsDTO(revoked_count=revoked_count)
