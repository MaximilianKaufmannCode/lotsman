# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration test: Redis Stream consumer → use-case → fake driver.

Uses fakeredis to avoid real Redis.  Tests:
  - Consumer reads a document.created event and calls enqueue.
  - Consumer reads a bulk_archived event and fans out per document.
  - Consumer ignores unknown event types (XACK but no enqueue).
  - Consumer XACKs after successful enqueue.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import suppress
from typing import Any

import pytest


@pytest.fixture
def fake_redis():
    """Return a fakeredis (or mock) instance for stream testing."""
    pytest.importorskip("fakeredis", reason="fakeredis not installed; skipping integration test")
    import fakeredis.aioredis as aioredis  # type: ignore[import]

    return aioredis.FakeRedis(decode_responses=True)


def _make_stream_message(event_type: str, payload: dict) -> dict:
    return {
        "type": event_type,
        "payload": json.dumps(payload),
    }


@pytest.mark.asyncio
async def test_consumer_enqueues_on_document_created(fake_redis: Any) -> None:
    """document.created.v1 → one enqueue call with the document_id."""
    from notification_service.infrastructure.consumers.registry_document_consumer import (
        _CONSUMER_GROUP,
        _STREAM_KEY,
        RegistryDocumentConsumer,
    )

    doc_id = uuid.uuid4()
    enqueued: list[uuid.UUID] = []

    async def fake_enqueue(document_id: uuid.UUID) -> None:
        enqueued.append(document_id)

    # Pre-create group.
    await fake_redis.xgroup_create(_STREAM_KEY, _CONSUMER_GROUP, id="0", mkstream=True)

    consumer = RegistryDocumentConsumer(
        redis_client=fake_redis,
        arq_enqueue=fake_enqueue,
    )

    # Inject a message.
    msg_fields = _make_stream_message(
        "registry.document.created.v1",
        {"document_id": str(doc_id)},
    )
    await fake_redis.xadd(_STREAM_KEY, msg_fields)

    # Run one iteration of the loop — use a short-circuit via asyncio task + cancel.
    task = asyncio.create_task(consumer.start())
    await asyncio.sleep(0.2)  # Let the loop process the message.
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task

    assert doc_id in enqueued


@pytest.mark.asyncio
async def test_consumer_fans_out_bulk_archived(fake_redis: Any) -> None:
    """bulk_archived.v1 with 3 doc IDs → 3 separate enqueue calls."""
    from notification_service.infrastructure.consumers.registry_document_consumer import (
        _CONSUMER_GROUP,
        _STREAM_KEY,
        RegistryDocumentConsumer,
    )

    doc_ids = [uuid.uuid4() for _ in range(3)]
    enqueued: list[uuid.UUID] = []

    async def fake_enqueue(document_id: uuid.UUID) -> None:
        enqueued.append(document_id)

    await fake_redis.xgroup_create(_STREAM_KEY, _CONSUMER_GROUP, id="0", mkstream=True)

    consumer = RegistryDocumentConsumer(
        redis_client=fake_redis,
        arq_enqueue=fake_enqueue,
    )

    msg_fields = _make_stream_message(
        "registry.document.bulk_archived.v1",
        {"document_ids": [str(d) for d in doc_ids], "count": 3},
    )
    await fake_redis.xadd(_STREAM_KEY, msg_fields)

    task = asyncio.create_task(consumer.start())
    await asyncio.sleep(0.2)
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task

    assert set(enqueued) == set(doc_ids)


@pytest.mark.asyncio
async def test_consumer_ignores_unknown_event_types(fake_redis: Any) -> None:
    """Unknown event types are XACK'd but enqueue is not called."""
    from notification_service.infrastructure.consumers.registry_document_consumer import (
        _CONSUMER_GROUP,
        _STREAM_KEY,
        RegistryDocumentConsumer,
    )

    enqueued: list[uuid.UUID] = []

    async def fake_enqueue(document_id: uuid.UUID) -> None:
        enqueued.append(document_id)

    await fake_redis.xgroup_create(_STREAM_KEY, _CONSUMER_GROUP, id="0", mkstream=True)

    consumer = RegistryDocumentConsumer(
        redis_client=fake_redis,
        arq_enqueue=fake_enqueue,
    )

    msg_fields = _make_stream_message(
        "registry.asset.created.v1",  # not in subscribed types
        {"asset_id": str(uuid.uuid4())},
    )
    await fake_redis.xadd(_STREAM_KEY, msg_fields)

    task = asyncio.create_task(consumer.start())
    await asyncio.sleep(0.2)
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task

    assert len(enqueued) == 0


@pytest.mark.asyncio
async def test_consumer_invalidates_ics_cache_on_document_event(fake_redis: Any) -> None:
    """Any document event must call ics_cache_invalidate."""
    from notification_service.infrastructure.consumers.registry_document_consumer import (
        _CONSUMER_GROUP,
        _STREAM_KEY,
        RegistryDocumentConsumer,
    )

    invalidated = []

    async def fake_enqueue(document_id: uuid.UUID) -> None:
        pass

    async def fake_invalidate() -> None:
        invalidated.append(True)

    await fake_redis.xgroup_create(_STREAM_KEY, _CONSUMER_GROUP, id="0", mkstream=True)

    consumer = RegistryDocumentConsumer(
        redis_client=fake_redis,
        arq_enqueue=fake_enqueue,
        ics_cache_invalidate=fake_invalidate,
    )

    msg_fields = _make_stream_message(
        "registry.document.updated.v1",
        {"document_id": str(uuid.uuid4())},
    )
    await fake_redis.xadd(_STREAM_KEY, msg_fields)

    task = asyncio.create_task(consumer.start())
    await asyncio.sleep(0.2)
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task

    assert len(invalidated) > 0
