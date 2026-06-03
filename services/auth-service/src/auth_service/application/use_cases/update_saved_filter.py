# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""UpdateSavedFilter use case — v1.23.0 registry-filters feature.

Partial update: only provided (non-None) fields are changed.

Business rules:
  1. Preset must belong to the requesting user_id.
  2. If name is changing, the new name must not already exist for this user.
  3. filter_json — if provided — must be a dict.
  4. is_default=True → unset old default first (at-most-one-default invariant).
  5. Publishes FilterPresetUpdated domain event via outbox in the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from auth_service.application.dto import SavedFilterDTO, UpdateSavedFilterCommand
from auth_service.application.ports import EventOutbox, SavedFilterRepository
from auth_service.domain.errors import (
    SavedFilterJsonInvalidError,
    SavedFilterNameTakenError,
    SavedFilterNotFoundError,
)
from auth_service.domain.events import FilterPresetUpdated


@dataclass(slots=True)
class UpdateSavedFilter:
    repo: SavedFilterRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: UpdateSavedFilterCommand) -> SavedFilterDTO:
        # Load and ownership-check
        entity = await self.repo.get_by_id(cmd.filter_id, cmd.user_id)
        if entity is None:
            raise SavedFilterNotFoundError

        now = datetime.now(tz=UTC)

        # Apply name change
        if cmd.name is not None and cmd.name != entity.name:
            if await self.repo.name_exists(cmd.user_id, cmd.name):
                raise SavedFilterNameTakenError
            entity.name = cmd.name  # type: ignore[attr-defined]

        # Apply filter_json change
        if cmd.filter_json is not None:
            if not isinstance(cmd.filter_json, dict):
                raise SavedFilterJsonInvalidError
            entity.filter_json = cmd.filter_json  # type: ignore[attr-defined]

        # Apply is_default change
        if cmd.is_default is not None:
            if cmd.is_default and not entity.is_default:
                await self.repo.unset_default_for_user(cmd.user_id)
            entity.is_default = cmd.is_default  # type: ignore[attr-defined]

        entity.updated_at = now  # type: ignore[attr-defined]
        await self.repo.update(entity)

        await self.outbox.publish(
            FilterPresetUpdated(
                actor_id=cmd.user_id,
                preset_id=entity.id,
                occurred_at=now,
            ).as_envelope()
        )

        return SavedFilterDTO.from_entity(entity)
