# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-12: List assets (partner companies), optional pg_trgm search."""

from __future__ import annotations

from dataclasses import dataclass

from registry_service.application.dto import AssetDTO
from registry_service.application.ports import AssetRepository


@dataclass(slots=True)
class ListAssets:
    repo: AssetRepository

    async def execute(
        self,
        *,
        q: str | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> list[AssetDTO]:
        assets = await self.repo.list_active(q=q, offset=offset, limit=limit)
        return [
            AssetDTO(
                id=a.id,
                name=a.name,
                inn=a.inn,
                notes=a.notes,
                status=a.status,
                created_at=a.created_at,
                updated_at=a.updated_at,
                deleted_at=a.deleted_at,
            )
            for a in assets
        ]
