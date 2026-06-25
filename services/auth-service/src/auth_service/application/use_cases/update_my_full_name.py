# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""UpdateMyFullName use case — user self-service full_name edit.

Input:  UpdateMyFullNameCommand(actor_id, full_name)
Output: UserDTO with updated full_name.

Business rules:
  - full_name.strip() must be 1..200 characters; raises ProfileValidationError otherwise.
  - Loads user; raises UserNotFoundError if not found.
  - Calls user_repo.update(user) — caller owns the transaction.
  - Emits auth.user.profile_updated.v1 to outbox in the same transaction.

Email change by the user is intentionally NOT supported (ADR-0003 §identity).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from auth_service.application.dto import UpdateMyFullNameCommand, UserDTO
from auth_service.application.ports import EventOutbox, UserRepository
from auth_service.domain.entities import UI_FONT_SCALE_MAX, UI_FONT_SCALE_MIN
from auth_service.domain.errors import ProfileValidationError, UserNotFoundError
from auth_service.domain.events import UserProfileUpdated


@dataclass(slots=True)
class UpdateMyFullName:
    user_repo: UserRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: UpdateMyFullNameCommand) -> UserDTO:
        new_full_name = cmd.full_name.strip()

        if len(new_full_name) == 0 or len(new_full_name) > 200:
            raise ProfileValidationError(
                "full_name must be between 1 and 200 characters after stripping whitespace"
            )

        # UI font-size preference is optional and validated defensively here —
        # the API also bounds it, but the use case never trusts an out-of-range
        # value (ADR-0003) so a misbehaving caller cannot break the layout.
        if cmd.ui_font_scale is not None and not (
            UI_FONT_SCALE_MIN <= cmd.ui_font_scale <= UI_FONT_SCALE_MAX
        ):
            raise ProfileValidationError(
                f"ui_font_scale must be between {UI_FONT_SCALE_MIN} and {UI_FONT_SCALE_MAX}"
            )

        user = await self.user_repo.get_by_id(cmd.actor_id)
        if user is None:
            raise UserNotFoundError()

        old_full_name = user.full_name
        name_changed = new_full_name != old_full_name
        user.full_name = new_full_name
        if cmd.ui_font_scale is not None:
            user.ui_font_scale = cmd.ui_font_scale
        user.updated_at = datetime.now(tz=UTC)

        await self.user_repo.update(user)

        # Emit the identity-change event ONLY when the name actually changed.
        # The SPA sends the current full_name alongside a font-size change, so a
        # font-only PATCH would otherwise spam the outbox with before==after.
        # The font-size preference is a private UI setting — intentionally not
        # an audited domain event.
        if name_changed:
            await self.outbox.publish(
                UserProfileUpdated(
                    actor_id=cmd.actor_id,
                    user_id=cmd.actor_id,
                    field="full_name",
                    before=old_full_name,
                    after=new_full_name,
                ).as_envelope()
            )

        return UserDTO.from_entity(user)
