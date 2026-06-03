# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Change the functional status of an asset (active | liquidating | archived).

When transitioning to 'archived':
  - Sets deleted_at = now() (dual-signal model; matches ArchiveAsset behaviour).
  - Cascade-archives all active documents belonging to the asset.

When transitioning to 'active' or 'liquidating' from 'archived':
  - Clears deleted_at (restores the asset from soft-delete).
  - Does NOT restore cascade-archived documents — that decision is left to the
    user (documents may have been archived for independent reasons).

The existing /archive endpoint continues to use ArchiveAsset which internally
calls this use case. This use case is the single point of truth for status changes.

Emits: registry.asset.status_changed.v1 via outbox.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from registry_service.application.dto import AssetDTO, ChangeAssetStatusCommand
from registry_service.application.ports import AssetRepository, Clock, EventOutbox
from registry_service.domain.entities import _ASSET_VALID_STATUSES
from registry_service.domain.errors import AssetNotFoundError, InvalidAssetStatusError
from registry_service.domain.events import AssetStatusChanged

_ARCHIVE_STATUS = "archived"


@dataclass(slots=True)
class ChangeAssetStatus:
    repo: AssetRepository
    outbox: EventOutbox
    clock: Clock

    async def execute(self, *, cmd: ChangeAssetStatusCommand) -> tuple[AssetDTO, int]:
        """Change asset status, maintaining dual-signal consistency.

        Returns:
            (AssetDTO, cascaded_document_count) — count is non-zero only when
            transitioning to 'archived'.

        Raises:
            AssetNotFoundError: if the asset does not exist.
            InvalidAssetStatusError: if the requested status is not valid.
        """
        if cmd.status not in _ASSET_VALID_STATUSES:
            raise InvalidAssetStatusError(
                f"Invalid status '{cmd.status}'. "
                f"Must be one of: {', '.join(sorted(_ASSET_VALID_STATUSES))}"
            )

        asset = await self.repo.get_by_id(cmd.asset_id)
        if asset is None:
            raise AssetNotFoundError

        before_status = asset.status
        now = self.clock.now()
        cascaded_count = 0

        if cmd.status == _ARCHIVE_STATUS:
            # Transitioning to archived: set both signals.
            # Cascade-archive all active documents.
            cascaded_count = await self.repo.archive_cascade_documents(cmd.asset_id, now)
            asset.deleted_at = now
        elif before_status == _ARCHIVE_STATUS:
            # Restoring from archived: clear the soft-delete signal.
            # Documents are NOT auto-restored (intentional — see module docstring).
            asset.deleted_at = None

        asset.status = cmd.status
        asset.updated_at = now
        await self.repo.update(asset)

        event = AssetStatusChanged(
            asset_id=asset.id,
            before=before_status,
            after=cmd.status,
            cascaded_document_count=cascaded_count,
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
        ), cascaded_count
