# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Outbox dispatcher ARQ task for registry-service.

Polls registry.outbox WHERE dispatched_at IS NULL using FOR UPDATE SKIP LOCKED,
XADDs each envelope to the matching Redis Stream, then marks dispatched_at.

See auth-service dispatcher for detailed design notes — this is the same
pattern applied to the registry schema (duplication is cheaper than wrong
abstraction per ADR-0002 backend handoff note).
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from typing import Any

import redis.asyncio as aioredis
from lotsman_shared.actors import ACTOR_OUTBOX_DISPATCHER
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

_BATCH_SIZE = 50
_STREAM_MAXLEN = 100_000


async def dispatch_outbox(ctx: dict[str, Any]) -> None:
    """ARQ task: poll registry.outbox and forward pending events to Redis Streams."""
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    redis: aioredis.Redis = ctx["redis"]

    async with session_factory() as session, session.begin():
        result = await session.execute(
            text(
                """
                    SELECT id, topic, payload
                    FROM registry.outbox
                    WHERE dispatched_at IS NULL
                    ORDER BY occurred_at
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                    """
            ),
            {"limit": _BATCH_SIZE},
        )
        rows = result.fetchall()

    if not rows:
        return

    dispatched_ids: list[str] = []
    for row in rows:
        row_id = str(row.id)
        topic: str = row.topic
        payload: dict[str, Any] = (
            row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
        )
        try:
            stream_fields = {k: json.dumps(v) for k, v in payload.items()}
            await redis.xadd(topic, stream_fields, maxlen=_STREAM_MAXLEN, approximate=True)  # type: ignore[arg-type]
            dispatched_ids.append(row_id)
        except Exception:
            log.exception("outbox_dispatch_failed", extra={"row_id": row_id, "topic": topic})

    if dispatched_ids:
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    """
                        UPDATE registry.outbox
                        SET dispatched_at = now()
                        WHERE id = ANY(:ids)
                        """
                ),
                {"ids": [_uuid.UUID(rid) for rid in dispatched_ids]},
            )
        log.info(
            "outbox_dispatched",
            extra={"count": len(dispatched_ids), "actor_id": str(ACTOR_OUTBOX_DISPATCHER)},
        )
