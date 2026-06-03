# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-19: Get field-level change history for an asset (calls audit-service)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from registry_service.application.ports import AuditServiceClient


@dataclass(slots=True)
class GetAssetHistory:
    audit_client: AuditServiceClient

    async def execute(
        self,
        *,
        asset_id: uuid.UUID,
        limit: int = 50,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.audit_client.get_events(
            entity_type="asset",
            entity_id=asset_id,
            limit=limit,
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )
