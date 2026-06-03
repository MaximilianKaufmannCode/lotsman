# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis Stream consumer for document-event notifications (ADR-0011 §D1).

Reads the same `registry.documents` stream as the calendar-sync consumer but
under an INDEPENDENT consumer group (`notification-events`) — independent cursors
mean this cannot disturb calendar sync. For each subscribed event it enqueues a
`process_document_event` ARQ job; the task does recipient resolution + delivery.

At-least-once: XACK only after the job is enqueued. process_document_event is
safe to re-run (it appends feed rows; duplicates are bounded and benign).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger(__name__)

_STREAM_KEY = "registry.documents"
_CONSUMER_GROUP = "notification-events"
_CONSUMER_NAME = "notification-events-1"
_BLOCK_MS = 5_000
_BATCH_SIZE = 10

_SUBSCRIBED_TYPES = {
    "registry.document.created.v1",
    "registry.document.updated.v1",
    "registry.document.archived.v1",
    "registry.document.restored.v1",
    "registry.document.bulk_archived.v1",
}


class EventNotificationConsumer:
    def __init__(self, redis_client: aioredis.Redis, enqueue_event: Any) -> None:
        self._redis = redis_client
        # async (event_type, payload, actor_id, event_id) -> None
        self._enqueue = enqueue_event
        self._running = False

    async def start(self) -> None:
        try:
            await self._redis.xgroup_create(
                _STREAM_KEY, _CONSUMER_GROUP, id="$", mkstream=True
            )
            log.info("event_consumer.group_created", group=_CONSUMER_GROUP)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            log.debug("event_consumer.group_exists", group=_CONSUMER_GROUP)
        self._running = True
        await self._run_loop()

    def stop(self) -> None:
        self._running = False

    async def _run_loop(self) -> None:
        log.info("event_consumer.loop_started", stream=_STREAM_KEY)
        while self._running:
            try:
                results = await self._redis.xreadgroup(
                    groupname=_CONSUMER_GROUP,
                    consumername=_CONSUMER_NAME,
                    streams={_STREAM_KEY: ">"},
                    count=_BATCH_SIZE,
                    block=_BLOCK_MS,
                )
                if not results:
                    continue
                for _stream, messages in results:
                    for msg_id, fields in messages:
                        await self._handle(msg_id, fields)
            except asyncio.CancelledError:
                log.info("event_consumer.cancelled")
                break
            except Exception:
                log.exception("event_consumer.loop_error")
                await asyncio.sleep(2)
        log.info("event_consumer.loop_stopped")

    async def _handle(self, msg_id: Any, fields: dict[Any, Any]) -> None:
        decoded = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in fields.items()
        }
        try:
            event_type = json.loads(decoded.get("type", '""'))
        except (ValueError, TypeError):
            event_type = decoded.get("type", "")

        if event_type not in _SUBSCRIBED_TYPES:
            await self._xack(msg_id)
            return

        try:
            payload = json.loads(decoded.get("payload", "{}"))
            actor_raw = decoded.get("actor_id")
            try:
                actor_id = json.loads(actor_raw) if actor_raw else None
            except (ValueError, TypeError):
                actor_id = actor_raw
            # Envelope id → idempotency key downstream (C1). Field is json-encoded.
            event_id_raw = decoded.get("id")
            try:
                event_id = json.loads(event_id_raw) if event_id_raw else None
            except (ValueError, TypeError):
                event_id = event_id_raw
            await self._enqueue(event_type, payload, actor_id, event_id)
        except Exception:
            log.exception("event_consumer.handle_failed", msg_id=msg_id)
            return  # leave in PEL for redelivery
        await self._xack(msg_id)

    async def _xack(self, msg_id: Any) -> None:
        try:
            await self._redis.xack(_STREAM_KEY, _CONSUMER_GROUP, msg_id)
        except Exception:
            log.warning("event_consumer.xack_failed", msg_id=msg_id)
