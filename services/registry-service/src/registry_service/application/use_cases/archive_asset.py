# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-15: Soft-delete an asset with cascade to active documents (admin-only).

Delegates to ChangeAssetStatus(status='archived') to keep the dual-signal
model consistent (status + deleted_at set atomically in one use case).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from registry_service.application.dto import ChangeAssetStatusCommand
from registry_service.application.ports import AssetRepository, Clock, EventOutbox
from registry_service.application.use_cases.change_asset_status import ChangeAssetStatus


@dataclass(slots=True)
class ArchiveAsset:
    repo: AssetRepository
    outbox: EventOutbox
    clock: Clock

    async def execute(
        self,
        *,
        asset_id: uuid.UUID,
        actor_id: uuid.UUID,
        request_id: str | None = None,
    ) -> int:
        """Archive the asset and cascade to active documents.

        Returns:
            Number of documents that were cascade-archived.
        """
        uc = ChangeAssetStatus(repo=self.repo, outbox=self.outbox, clock=self.clock)
        _dto, cascaded_count = await uc.execute(
            cmd=ChangeAssetStatusCommand(
                asset_id=asset_id,
                status="archived",
                actor_id=actor_id,
                request_id=request_id,
            )
        )
        return cascaded_count
