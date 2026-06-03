# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""GetMyProfile use case — user self-service profile read.

Input:  GetMyProfileCommand(actor_id)
Output: UserDTO for the authenticated user.

This is a pure read — no mutations, no events.
"""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import GetMyProfileCommand, UserDTO
from auth_service.application.ports import UserRepository
from auth_service.domain.errors import UserNotFoundError


@dataclass(slots=True)
class GetMyProfile:
    user_repo: UserRepository

    async def execute(self, *, cmd: GetMyProfileCommand) -> UserDTO:
        user = await self.user_repo.get_by_id(cmd.actor_id)
        if user is None:
            raise UserNotFoundError()
        return UserDTO.from_entity(user)
