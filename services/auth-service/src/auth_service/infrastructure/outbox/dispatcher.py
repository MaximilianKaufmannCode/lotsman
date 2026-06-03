# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Outbox dispatcher ARQ task for auth-service.

Polls auth.outbox WHERE dispatched_at IS NULL using FOR UPDATE SKIP LOCKED
(one row at a time, batch-safe), XADDs each envelope to the matching
Redis Stream, then marks dispatched_at = now().

This task runs as a recurring ARQ cron job every ~1 second.

Consumer group naming convention: <consumer-service>-<purpose>
Stream naming: matches the `topic` column in auth.outbox (e.g. 'auth.users').
Redis Stream MAXLEN: ~100000 with approximate trimming (14-day retention per ADR-0002 §C).

On crash mid-publish: dispatched_at stays NULL, the row is retried on the next
poll cycle. At-least-once delivery — consumers must be idempotent (envelope.id).
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


async def dispatch_outbox(
    ctx: dict[str, Any],
) -> None:
    """ARQ task: poll auth.outbox and forward pending events to Redis Streams.

    Expected in ARQ worker context:
        ctx["session_factory"] — async_sessionmaker[AsyncSession]
        ctx["redis"]           — aioredis.Redis client
    """
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    redis: aioredis.Redis = ctx["redis"]

    async with session_factory() as session:
        async with session.begin():
            # Select up to BATCH_SIZE undispatched rows with row-level locking.
            # SKIP LOCKED prevents multiple workers from picking the same row.
            result = await session.execute(
                text(
                    """
                    SELECT id, topic, payload
                    FROM auth.outbox
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
            row_id: str = str(row.id)
            topic: str = row.topic
            payload: dict[str, Any] = (
                row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
            )
            try:
                # Encode payload values as strings for Redis Streams.
                stream_fields = {k: json.dumps(v) for k, v in payload.items()}
                await redis.xadd(
                    topic,
                    stream_fields,  # type: ignore[arg-type]
                    maxlen=_STREAM_MAXLEN,
                    approximate=True,
                )
                dispatched_ids.append(row_id)
            except Exception:
                log.exception(
                    "outbox_dispatch_failed",
                    extra={"row_id": row_id, "topic": topic},
                )
                # Leave dispatched_at NULL so it retries next cycle.

        if dispatched_ids:
            async with session_factory() as session, session.begin():
                await session.execute(
                    text(
                        """
                            UPDATE auth.outbox
                            SET dispatched_at = now()
                            WHERE id = ANY(:ids)
                            """
                    ),
                    {"ids": [_uuid.UUID(i) for i in dispatched_ids]},
                )
            log.info(
                "outbox_dispatched",
                extra={"count": len(dispatched_ids), "actor_id": str(ACTOR_OUTBOX_DISPATCHER)},
            )
