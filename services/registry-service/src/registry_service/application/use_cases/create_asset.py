# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-13: Create a new asset (admin-only). INN validated per Q6."""

from __future__ import annotations

from dataclasses import dataclass

from registry_service.application.dto import AssetDTO, CreateAssetCommand
from registry_service.application.policies.inn_policy import validate_inn
from registry_service.application.ports import AssetRepository, Clock, EventOutbox
from registry_service.domain.entities import Asset
from registry_service.domain.errors import AssetAlreadyExistsError, InnInvalidError
from registry_service.domain.events import AssetCreated


@dataclass(slots=True)
class CreateAsset:
    repo: AssetRepository
    outbox: EventOutbox
    clock: Clock

    async def execute(self, *, cmd: CreateAssetCommand) -> AssetDTO:
        # Validate INN if provided
        if cmd.inn is not None:
            result = validate_inn(cmd.inn)
            if not result.valid:
                raise InnInvalidError(result.error or "INN is invalid")

        # Enforce partial unique constraint (active names only)
        if await self.repo.name_exists_for_active(cmd.name):
            raise AssetAlreadyExistsError

        now = self.clock.now()
        asset = Asset.create(
            name=cmd.name,
            inn=cmd.inn,
            notes=cmd.notes,
            now=now,
        )

        await self.repo.add(asset)

        event = AssetCreated(
            asset_id=asset.id,
            name=asset.name,
            inn=asset.inn,
            actor_id=cmd.actor_id,
            request_id=cmd.request_id,
            occurred_at=now,
        )
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
