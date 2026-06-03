# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""AdminUpdateUserProfile use case — US-103.

Admin edits another user's profile field (currently: full_name).

Mirrors UpdateMyFullName but with two key differences:
- target_user_id ≠ actor_id (admin acts on another user).
- Emits UserProfileUpdated with `actor_id = admin_id`, `user_id = target_id`
  so the audit trail correctly attributes the change to the admin.

Email change is intentionally NOT here — it has the same security concerns as
self-service email change (verification flow) and belongs to a separate ADR.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from auth_service.application.dto import AdminUpdateUserProfileCommand, UserDTO
from auth_service.application.ports import EventOutbox, UserRepository
from auth_service.domain.errors import (
    ProfileValidationError,
    SelfActionForbiddenError,
    UserNotFoundError,
)
from auth_service.domain.events import UserProfileUpdated


@dataclass(slots=True)
class AdminUpdateUserProfile:
    user_repo: UserRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: AdminUpdateUserProfileCommand) -> UserDTO:
        if cmd.actor_id == cmd.target_user_id:
            raise SelfActionForbiddenError(
                "Admins update their own profile via /api/v1/me, not /admin/users"
            )

        new_full_name = cmd.full_name.strip()
        if len(new_full_name) == 0 or len(new_full_name) > 200:
            raise ProfileValidationError(
                "full_name must be between 1 and 200 characters after stripping whitespace"
            )

        user = await self.user_repo.get_by_id(cmd.target_user_id)
        if user is None:
            raise UserNotFoundError()

        # Idempotent: same name → skip update + event.
        if user.full_name == new_full_name:
            return UserDTO.from_entity(user)

        old_full_name = user.full_name
        user.full_name = new_full_name
        user.updated_at = datetime.now(tz=UTC)

        await self.user_repo.update(user)

        await self.outbox.publish(
            UserProfileUpdated(
                actor_id=cmd.actor_id,
                user_id=cmd.target_user_id,
                field="full_name",
                before=old_full_name,
                after=new_full_name,
            ).as_envelope()
        )

        return UserDTO.from_entity(user)
