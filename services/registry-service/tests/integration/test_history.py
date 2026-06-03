# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Integration tests for document / asset history proxying — US-18, US-19.

The registry-service delegates history queries to audit-service via the
AuditServiceClient port. These tests mock the HTTP client with respx to verify
the proxy behavior without a running audit-service.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.skipif(
    True,
    reason=(
        "Requires testcontainers[postgres] + asyncpg + respx at runtime. "
        "Unblock by installing: uv add --dev 'testcontainers[postgres]' asyncpg respx"
    ),
)

# ---------------------------------------------------------------------------
# Fake audit client (respx-based mock)
# ---------------------------------------------------------------------------


class FakeAuditClient:
    """In-memory stub for AuditServiceClient. Allows injecting canned responses."""

    def __init__(
        self, events: list[dict] | None = None, raise_exc: Exception | None = None
    ) -> None:
        self._events = events or []
        self._raise = raise_exc
        self.calls: list[dict] = []

    async def get_events(
        self,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        limit: int = 50,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> list[dict]:
        self.calls.append({"entity_type": entity_type, "entity_id": entity_id, "limit": limit})
        if self._raise:
            raise self._raise
        return self._events


# ---------------------------------------------------------------------------
# US-18 — Document history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_history_calls_audit_client_and_returns_events(session, clock):
    """GetDocumentHistory proxies to audit client and returns mapped events."""
    from registry_service.application.use_cases.get_document_history import GetDocumentHistory

    doc_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    canned_events = [
        {
            "occurred_at": "2026-05-07T10:00:00Z",
            "actor_id": str(actor_id),
            "event_type": "updated",
            "field": "number",
            "before": "OLD-001",
            "after": "NEW-001",
        }
    ]
    audit_client = FakeAuditClient(events=canned_events)

    sut = GetDocumentHistory(audit_client=audit_client)  # type: ignore[arg-type]
    events = await sut.execute(
        document_id=doc_id,
        actor_id=actor_id,
        role="viewer",
        limit=50,
    )

    assert len(events) == 1
    assert events[0]["field"] == "number"
    assert events[0]["before"] == "OLD-001"
    assert len(audit_client.calls) == 1
    assert audit_client.calls[0]["entity_type"] == "document"
    assert audit_client.calls[0]["entity_id"] == doc_id


@pytest.mark.asyncio
async def test_get_document_history_empty_returns_empty_list(session, clock):
    """Newly created document has no audit events: returns empty list."""
    from registry_service.application.use_cases.get_document_history import GetDocumentHistory

    audit_client = FakeAuditClient(events=[])
    sut = GetDocumentHistory(audit_client=audit_client)  # type: ignore[arg-type]
    events = await sut.execute(
        document_id=uuid.uuid4(), actor_id=uuid.uuid4(), role="viewer", limit=50
    )
    assert events == []


@pytest.mark.asyncio
async def test_get_document_history_audit_service_unavailable_propagates_error(session, clock):
    """When audit-service raises an exception, it propagates to the caller."""
    from registry_service.application.use_cases.get_document_history import GetDocumentHistory

    audit_client = FakeAuditClient(raise_exc=ConnectionError("audit-service 503"))
    sut = GetDocumentHistory(audit_client=audit_client)  # type: ignore[arg-type]

    with pytest.raises(ConnectionError):
        await sut.execute(document_id=uuid.uuid4(), actor_id=uuid.uuid4(), role="viewer", limit=50)


# ---------------------------------------------------------------------------
# US-19 — Asset history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_asset_history_calls_audit_client(session, clock):
    """GetAssetHistory proxies to audit client with entity_type=asset."""
    from registry_service.application.use_cases.get_asset_history import GetAssetHistory

    asset_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    canned = [
        {
            "occurred_at": "2026-05-06T09:00:00Z",
            "actor_id": str(actor_id),
            "event_type": "updated",
            "field": "name",
            "before": "ООО Ромашка",
            "after": "ООО Ромашка Плюс",
        }
    ]
    audit_client = FakeAuditClient(events=canned)
    sut = GetAssetHistory(audit_client=audit_client)  # type: ignore[arg-type]

    events = await sut.execute(asset_id=asset_id, actor_id=actor_id, role="viewer", limit=50)

    assert len(events) == 1
    assert events[0]["field"] == "name"
    assert audit_client.calls[0]["entity_type"] == "asset"
    assert audit_client.calls[0]["entity_id"] == asset_id


@pytest.mark.asyncio
async def test_get_archived_asset_history_still_returns_events(session, clock):
    """Archiving an asset does not purge its audit history."""
    from registry_service.application.use_cases.get_asset_history import GetAssetHistory

    canned = [
        {
            "occurred_at": "2026-05-07T12:00:00Z",
            "actor_id": str(uuid.uuid4()),
            "event_type": "archived",
            "field": "deleted_at",
            "before": None,
            "after": "2026-05-07T12:00:00Z",
        }
    ]
    audit_client = FakeAuditClient(events=canned)
    sut = GetAssetHistory(audit_client=audit_client)  # type: ignore[arg-type]

    events = await sut.execute(asset_id=uuid.uuid4(), actor_id=uuid.uuid4(), role="admin", limit=50)
    assert any(e["event_type"] == "archived" for e in events)


# ---------------------------------------------------------------------------
# US-18 — Outbox events correlation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbox_event_has_actor_id_and_request_id(session, clock):
    """Every state-changing use case writes outbox row with actor_id + request_id."""
    import uuid
    from datetime import UTC, datetime

    from sqlalchemy import select

    from registry_service.application.dto import CreateDocumentCommand
    from registry_service.application.use_cases.create_document import CreateDocument
    from registry_service.db.models import Outbox
    from registry_service.domain.entities import Asset, DocumentType
    from registry_service.infrastructure.db.repositories import (
        SqlAssetRepository,
        SqlDocumentRepository,
        SqlDocumentTypeRepository,
        SqlEventOutbox,
    )

    now = datetime.now(tz=UTC)
    asset = Asset(
        id=uuid.uuid4(),
        name="ООО Корреляция",
        inn=None,
        notes=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    doc_type = DocumentType(
        code="contract",
        display_name="Договор",
        pre_notice_days=[30],
        notify_in_day=True,
        overdue_every_days=7,
        created_at=now,
        updated_at=now,
    )

    asset_repo = SqlAssetRepository(session)
    doc_repo = SqlDocumentRepository(session)
    type_repo = SqlDocumentTypeRepository(session)
    outbox = SqlEventOutbox(session)

    await asset_repo.add(asset)
    await type_repo.upsert(doc_type)

    sut = CreateDocument(
        doc_repo=doc_repo, asset_repo=asset_repo, type_repo=type_repo, outbox=outbox, clock=clock
    )
    actor_id = uuid.uuid4()
    req_id = "req_correlation_test"

    await sut.execute(
        cmd=CreateDocumentCommand(
            asset_id=asset.id,
            type_code="contract",
            number="CORR-001",
            issue_date=None,
            expiry_date=None,
            responsible_user_id=None,
            notes=None,
            actor_id=actor_id,
            request_id=req_id,
        )
    )

    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.documents"))
    rows = result.scalars().all()
    assert len(rows) >= 1

    envelope = rows[0].payload
    assert envelope.get("actor_id") == str(actor_id)
    assert envelope.get("request_id") == req_id


@pytest.mark.asyncio
async def test_outbox_dispatcher_publishes_to_correct_stream(session, clock):
    """Outbox rows for assets go to registry.assets, documents to registry.documents."""
    from sqlalchemy import select

    from registry_service.application.dto import CreateAssetCommand
    from registry_service.application.use_cases.create_asset import CreateAsset
    from registry_service.db.models import Outbox
    from registry_service.infrastructure.db.repositories import SqlAssetRepository, SqlEventOutbox

    repo = SqlAssetRepository(session)
    outbox = SqlEventOutbox(session)
    sut = CreateAsset(repo=repo, outbox=outbox, clock=clock)  # type: ignore[arg-type]

    await sut.execute(
        cmd=CreateAssetCommand(name="ООО Поток", inn=None, notes=None, actor_id=uuid.uuid4())
    )

    result = await session.execute(select(Outbox).where(Outbox.topic == "registry.assets"))
    rows = result.scalars().all()
    assert len(rows) >= 1
    assert all(r.topic == "registry.assets" for r in rows)
