# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Repository port protocols for audit-service.

Audit-service is a terminal sink — it has no outbox and never publishes events.
It only reads (via its consumer) and writes to audit.events (append-only).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol


class AuditEventRepository(Protocol):
    """Port for writing to the append-only audit.events table."""

    async def record(
        self,
        *,
        event_id: uuid.UUID,
        occurred_at: datetime,
        actor_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        event_type: str,
        payload: dict[str, object],
        request_id: str | None,
    ) -> None:
        """Insert a new audit event row. Raises DuplicateEventError on conflict."""
        ...

    async def list_for_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        limit: int = 50,
    ) -> list[object]:
        """Return audit events for a given entity, newest first."""
        ...
