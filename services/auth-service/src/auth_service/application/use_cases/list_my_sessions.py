# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ListMySessions use case — US-20."""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import ListMySessionsCommand, SessionDTO
from auth_service.application.ports import SessionRepository


@dataclass(slots=True)
class ListMySessions:
    session_repo: SessionRepository

    async def execute(self, *, cmd: ListMySessionsCommand) -> list[SessionDTO]:
        sessions = await self.session_repo.list_active_for_user(cmd.user_id)
        return [
            SessionDTO.from_entity(s, current_session_id=cmd.current_session_id) for s in sessions
        ]
