# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""DeleteSavedFilter use case — v1.23.0 registry-filters feature.

Hard-deletes the preset. Publishes FilterPresetDeleted domain event in the same
transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from auth_service.application.dto import DeleteSavedFilterCommand
from auth_service.application.ports import EventOutbox, SavedFilterRepository
from auth_service.domain.errors import SavedFilterNotFoundError
from auth_service.domain.events import FilterPresetDeleted


@dataclass(slots=True)
class DeleteSavedFilter:
    repo: SavedFilterRepository
    outbox: EventOutbox

    async def execute(self, *, cmd: DeleteSavedFilterCommand) -> None:
        entity = await self.repo.get_by_id(cmd.filter_id, cmd.user_id)
        if entity is None:
            raise SavedFilterNotFoundError

        now = datetime.now(tz=UTC)
        await self.repo.delete(cmd.filter_id)

        await self.outbox.publish(
            FilterPresetDeleted(
                actor_id=cmd.user_id,
                preset_id=cmd.filter_id,
                occurred_at=now,
            ).as_envelope()
        )
