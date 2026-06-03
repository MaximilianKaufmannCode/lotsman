# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ListUserSessionsAdmin use case — US-21."""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import ListUserSessionsAdminCommand, SessionDTO
from auth_service.application.ports import SessionRepository, UserRepository
from auth_service.domain.errors import UserNotFoundError


@dataclass(slots=True)
class ListUserSessionsAdmin:
    user_repo: UserRepository
    session_repo: SessionRepository

    async def execute(self, *, cmd: ListUserSessionsAdminCommand) -> list[SessionDTO]:
        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()
        sessions = await self.session_repo.list_active_for_user(cmd.target_user_id)
        return [SessionDTO.from_entity(s) for s in sessions]
