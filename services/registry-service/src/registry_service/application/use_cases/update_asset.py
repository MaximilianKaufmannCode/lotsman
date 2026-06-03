# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-14: Update asset fields (admin-only)."""

from __future__ import annotations

from dataclasses import dataclass

from registry_service.application.dto import AssetDTO, UpdateAssetCommand
from registry_service.application.policies.inn_policy import validate_inn
from registry_service.application.ports import AssetRepository, Clock, EventOutbox
from registry_service.domain.errors import AssetArchivedError, InnInvalidError
from registry_service.domain.events import AssetUpdated


@dataclass(slots=True)
class UpdateAsset:
    repo: AssetRepository
    outbox: EventOutbox
    clock: Clock

    async def execute(self, *, cmd: UpdateAssetCommand) -> AssetDTO:
        # Only active assets can be updated (US-14 AC)
        asset = await self.repo.get_active_by_id(cmd.asset_id)
        if asset is None:
            raise AssetArchivedError("Asset not found or archived")

        if cmd.inn is not None:
            result = validate_inn(cmd.inn)
            if not result.valid:
                raise InnInvalidError(result.error or "INN is invalid")

        now = self.clock.now()
        events: list[AssetUpdated] = []

        if cmd.name is not None and cmd.name != asset.name:
            events.append(
                AssetUpdated(
                    asset_id=asset.id,
                    field="name",
                    before=asset.name,
                    after=cmd.name,
                    actor_id=cmd.actor_id,
                    request_id=cmd.request_id,
                    occurred_at=now,
                )
            )
            asset.name = cmd.name

        if cmd.inn is not None and cmd.inn != asset.inn:
            events.append(
                AssetUpdated(
                    asset_id=asset.id,
                    field="inn",
                    before=asset.inn,
                    after=cmd.inn,
                    actor_id=cmd.actor_id,
                    request_id=cmd.request_id,
                    occurred_at=now,
                )
            )
            asset.inn = cmd.inn

        if cmd.notes is not None and cmd.notes != asset.notes:
            events.append(
                AssetUpdated(
                    asset_id=asset.id,
                    field="notes",
                    before=asset.notes,
                    after=cmd.notes,
                    actor_id=cmd.actor_id,
                    request_id=cmd.request_id,
                    occurred_at=now,
                )
            )
            asset.notes = cmd.notes

        if events:
            asset.updated_at = now
            await self.repo.update(asset)
            for event in events:
                await self.outbox.publish(event.as_envelope(), topic=event.topic)

        return AssetDTO(
            id=asset.id,
            name=asset.name,
            inn=asset.inn,
            notes=asset.notes,
            status=asset.status,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
            deleted_at=asset.deleted_at,
        )
