# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""CreateSavedFilter use case — v1.23.0 registry-filters feature.

Business rules enforced here (not at DB layer):
  1. filter_json MUST be a dict (JSON object), not a list, scalar, or None.
  2. name MUST be 1–100 characters (enforced by DTO validation before reaching here).
  3. Maximum 20 presets per user.
  4. Name must be unique per user (case-sensitive).
  5. is_default=True → unset all other defaults for the user first (at-most-one-default invariant).
  6. Publishes FilterPresetSaved domain event via outbox in the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from auth_service.application.dto import CreateSavedFilterCommand, SavedFilterDTO
from auth_service.application.ports import EventOutbox, SavedFilterRepository
from auth_service.domain.entities import SavedFilter
from auth_service.domain.errors import (
    SavedFilterJsonInvalidError,
    SavedFilterLimitExceededError,
    SavedFilterNameTakenError,
)
from auth_service.domain.events import FilterPresetSaved

_MAX_PRESETS_PER_USER = 20


@dataclass(slots=True)
class CreateSavedFilter:
    repo: SavedFilterRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: CreateSavedFilterCommand) -> SavedFilterDTO:
        # Rule 1 — filter_json must be a dict (JSON object)
        if not isinstance(cmd.filter_json, dict):
            raise SavedFilterJsonInvalidError

        # Rule 3 — limit 20 presets per user
        current_count = await self.repo.count_for_user(cmd.user_id)
        if current_count >= _MAX_PRESETS_PER_USER:
            raise SavedFilterLimitExceededError

        # Rule 4 — unique name per user
        if await self.repo.name_exists(cmd.user_id, cmd.name):
            raise SavedFilterNameTakenError

        # Rule 5 — at-most-one default per user
        if cmd.is_default:
            await self.repo.unset_default_for_user(cmd.user_id)

        now = datetime.now(tz=UTC)
        entity = SavedFilter.create(
            user_id=cmd.user_id,
            name=cmd.name,
            filter_json=cmd.filter_json,
            is_default=cmd.is_default,
            now=now,
        )

        await self.repo.add(entity)

        # Rule 6 — domain event in same transaction (Iron Rule 6)
        await self.outbox.publish(
            FilterPresetSaved(
                actor_id=cmd.user_id,
                preset_id=entity.id,
                name=entity.name,
                occurred_at=now,
            ).as_envelope()
        )

        return SavedFilterDTO.from_entity(entity)
