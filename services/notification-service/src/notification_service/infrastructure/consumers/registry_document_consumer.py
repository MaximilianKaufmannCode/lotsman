# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis Stream consumer for registry.documents events.

Subscribes to the stream produced by registry-service's outbox dispatcher.
Consumer group: notification-calendar-sync (separate from any other notification
consumers — clean isolation per ADR-0005 §5).

Handled event types:
  - registry.document.created.v1
  - registry.document.updated.v1
  - registry.document.archived.v1
  - registry.document.restored.v1
  - registry.document.bulk_archived.v1  (fan-out: one ARQ task per document_id)

Side effect: invalidates the in-memory ICS feed cache on any document event.

Delivery: at-least-once. XACK only after ARQ task is enqueued. The
sync_calendar_event task itself is idempotent.

Usage — called from notification_service/main.py lifespan as a long-running
asyncio task.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger(__name__)

_STREAM_KEY = "registry.documents"
_CONSUMER_GROUP = "notification-calendar-sync"
_CONSUMER_NAME = "notification-svc-1"
_BLOCK_MS = 5_000
_BATCH_SIZE = 10

# Event types we care about.
_SUBSCRIBED_TYPES = {
    "registry.document.created.v1",
    "registry.document.updated.v1",
    "registry.document.archived.v1",
    "registry.document.restored.v1",
    "registry.document.bulk_archived.v1",
}


class RegistryDocumentConsumer:
    """Runs a Redis XREADGROUP loop and enqueues ARQ tasks on document events.

    Args:
        redis_client: Shared aioredis.Redis instance.
        arq_enqueue: Callable that enqueues a sync task: async (document_id) -> None.
        ics_cache_invalidate: Optional callable to invalidate ICS in-memory cache.
        stream_key: Stream key to consume (default: 'registry.documents').
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        arq_enqueue: Any,
        ics_cache_invalidate: Any | None = None,
        stream_key: str = _STREAM_KEY,
    ) -> None:
        self._redis = redis_client
        self._enqueue = arq_enqueue
        self._ics_invalidate = ics_cache_invalidate
        self._stream_key = stream_key
        self._running = False

    async def start(self) -> None:
        """Ensure the consumer group exists and start the read loop."""
        try:
            await self._redis.xgroup_create(
                self._stream_key,
                _CONSUMER_GROUP,
                id="$",   # only new messages
                mkstream=True,
            )
            log.info(
                "registry_consumer.group_created",
                stream=self._stream_key,
                group=_CONSUMER_GROUP,
            )
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise  # Unexpected — propagate.
            log.debug("registry_consumer.group_already_exists", group=_CONSUMER_GROUP)

        self._running = True
        await self._run_loop()

    def stop(self) -> None:
        """Signal the read loop to exit cleanly."""
        self._running = False

    async def _run_loop(self) -> None:
        log.info("registry_consumer.loop_started", stream=self._stream_key)
        while self._running:
            try:
                results = await self._redis.xreadgroup(
                    groupname=_CONSUMER_GROUP,
                    consumername=_CONSUMER_NAME,
                    streams={self._stream_key: ">"},
                    count=_BATCH_SIZE,
                    block=_BLOCK_MS,
                )
                if not results:
                    continue
                for _stream, messages in results:
                    for msg_id, fields in messages:
                        await self._handle_message(msg_id, fields)
            except asyncio.CancelledError:
                log.info("registry_consumer.cancelled")
                break
            except Exception:
                log.exception("registry_consumer.loop_error")
                await asyncio.sleep(2)

        log.info("registry_consumer.loop_stopped")

    async def _handle_message(
        self, msg_id: str | bytes, fields: dict[Any, Any]
    ) -> None:
        """Dispatch a single stream message to ARQ."""
        # Decode bytes keys/values if needed.
        decoded: dict[str, str] = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in fields.items()
        }
        # Each field value was json.dumps-encoded by the outbox dispatcher
        # (see services/registry-service/.../outbox/dispatcher.py).
        # Strip JSON-quoting from scalar fields like `type` before lookup.
        raw_type = decoded.get("type", '""')
        try:
            event_type = json.loads(raw_type)
        except (ValueError, TypeError):
            event_type = raw_type

        if event_type not in _SUBSCRIBED_TYPES:
            # XACK unknown events so they don't pile up in PEL.
            await self._xack(msg_id)
            return

        try:
            payload_raw = decoded.get("payload", "{}")
            payload: dict[str, Any] = json.loads(payload_raw)

            if event_type == "registry.document.bulk_archived.v1":
                doc_ids = payload.get("document_ids", [])
                for raw_id in doc_ids:
                    await self._enqueue(uuid.UUID(str(raw_id)))
            else:
                raw_id = payload.get("document_id")
                if raw_id:
                    await self._enqueue(uuid.UUID(str(raw_id)))

            # Invalidate ICS cache on any document change.
            if self._ics_invalidate is not None:
                try:
                    await self._ics_invalidate()
                except Exception:
                    log.warning("ics_cache_invalidate_failed")

        except Exception:
            log.exception("registry_consumer.handle_failed", msg_id=msg_id, event_type=event_type)
            # Do NOT XACK — let PEL retain it for manual inspection / re-delivery.
            return

        await self._xack(msg_id)

    async def _xack(self, msg_id: str | bytes) -> None:
        try:
            await self._redis.xack(self._stream_key, _CONSUMER_GROUP, msg_id)
        except Exception:
            log.warning("registry_consumer.xack_failed", msg_id=msg_id)
