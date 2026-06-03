# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for EventNotifier routing/coalescing (ADR-0011 Phase 2).

Delivery (DB writes/email) is covered separately; here we assert the routing:
which events coalesce vs deliver immediately, and to whom.
"""

from __future__ import annotations

import uuid

import pytest

from notification_service.application.use_cases.event_notifications import EventNotifier
from notification_service.domain import document_events as de

DOC = uuid.uuid4()
ACTOR = uuid.uuid4()
ASSIGNEE = uuid.uuid4()


class _RecordingNotifier(EventNotifier):
    """Captures _deliver_for_document calls instead of touching the DB."""

    def __init__(self, **kw):  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self.delivered: list[dict] = []

    async def _deliver_for_document(self, **kwargs):  # type: ignore[no-untyped-def]
        self.delivered.append(kwargs)


class _FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list] = {}
        self.kv: dict[str, str] = {}

    async def rpush(self, key, val):  # type: ignore[no-untyped-def]
        self.lists.setdefault(key, []).append(val)

    async def expire(self, key, ttl):  # type: ignore[no-untyped-def]
        pass

    async def set(self, key, val, ex=None):  # type: ignore[no-untyped-def]
        self.kv[key] = val


class _FakeArq:
    def __init__(self) -> None:
        self.jobs: list[tuple] = []

    async def enqueue_job(self, name, *args, **kw):  # type: ignore[no-untyped-def]
        self.jobs.append((name, args, kw))


def _make(redis=None, arq=None):  # type: ignore[no-untyped-def]
    return _RecordingNotifier(
        session_factory=None,
        auth_gateway=object(),
        registry_gateway=object(),
        redis=redis,
        arq_pool=arq,
    )


@pytest.mark.asyncio
async def test_created_delivers_immediately() -> None:
    n = _make()
    res = await n.process_event(
        event_type="registry.document.created.v1",
        payload={"document_id": str(DOC)},
        actor_id=ACTOR,
    )
    assert res == "delivered"
    assert len(n.delivered) == 1
    assert n.delivered[0]["category"] == de.DOC_CREATED


@pytest.mark.asyncio
async def test_plain_update_is_coalesced() -> None:
    redis, arq = _FakeRedis(), _FakeArq()
    n = _make(redis=redis, arq=arq)
    res = await n.process_event(
        event_type="registry.document.updated.v1",
        payload={"document_id": str(DOC), "field": "expiry_date"},
        actor_id=ACTOR,
    )
    assert res == "buffered"
    assert n.delivered == []  # not delivered immediately
    assert redis.lists[f"evtbuf:{DOC}"] == ["expiry_date"]
    name, args, kw = arq.jobs[0]
    assert name == "flush_document_update"
    # C2: per-window job id (doc + window bucket) + window passed as arg
    assert kw.get("_job_id", "").startswith(f"flushupd:{DOC}:")
    assert args[0] == str(DOC)
    assert isinstance(args[1], int)  # window bucket
    # job id window suffix matches the window arg
    assert kw["_job_id"] == f"flushupd:{DOC}:{args[1]}"


@pytest.mark.asyncio
async def test_immediate_dedup_key_threaded() -> None:
    n = _make()
    await n.process_event(
        event_type="registry.document.created.v1",
        payload={"document_id": str(DOC)},
        actor_id=ACTOR,
        event_id="evt-123",
    )
    # C1: dedup_key = {event_id}:{document_id}
    assert n.delivered[0]["dedup_key"] == f"evt-123:{DOC}"


@pytest.mark.asyncio
async def test_update_without_coalesce_infra_delivers() -> None:
    n = _make(redis=None, arq=None)  # no buffer infra → immediate fallback
    await n.process_event(
        event_type="registry.document.updated.v1",
        payload={"document_id": str(DOC), "field": "status"},
        actor_id=ACTOR,
    )
    assert n.delivered[0]["category"] == de.DOC_UPDATED
    assert n.delivered[0]["fields"] == ["status"]


@pytest.mark.asyncio
async def test_assigned_targets_assignee_only() -> None:
    n = _make()
    res = await n.process_event(
        event_type="registry.document.updated.v1",
        payload={"document_id": str(DOC), "field": "responsible_user_id", "after": str(ASSIGNEE)},
        actor_id=ACTOR,
    )
    assert res == "delivered"
    d = n.delivered[0]
    assert d["category"] == de.DOC_ASSIGNED
    assert d["target_user_ids"] == [ASSIGNEE]


@pytest.mark.asyncio
async def test_attachment_maps_to_attachment_category() -> None:
    n = _make()
    await n.process_event(
        event_type="registry.document.updated.v1",
        payload={"document_id": str(DOC), "field": "attachments", "after": {"x": 1}},
        actor_id=ACTOR,
    )
    assert n.delivered[0]["category"] == de.DOC_ATTACHMENT


@pytest.mark.asyncio
async def test_bulk_archive_delivers_per_document() -> None:
    n = _make()
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    res = await n.process_event(
        event_type="registry.document.bulk_archived.v1",
        payload={"document_ids": [str(d1), str(d2)]},
        actor_id=ACTOR,
    )
    assert res == "delivered:2"
    assert {d["document_id"] for d in n.delivered} == {d1, d2}
    assert all(d["category"] == de.DOC_ARCHIVED for d in n.delivered)


@pytest.mark.asyncio
async def test_unknown_event_ignored() -> None:
    n = _make()
    res = await n.process_event(
        event_type="registry.asset.created.v1", payload={}, actor_id=ACTOR
    )
    assert res == "ignored"
    assert n.delivered == []
