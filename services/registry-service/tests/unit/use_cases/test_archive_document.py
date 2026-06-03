# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for ArchiveDocument (US-6) and RestoreDocument (US-7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.use_cases.archive_document import ArchiveDocument
from registry_service.application.use_cases.restore_document import RestoreDocument
from registry_service.domain.entities import Document
from registry_service.domain.errors import DocumentNotFoundError
from tests.unit.use_cases.fakes import (
    FakeClock,
    FakeDocumentRepository,
    FakeEventOutbox,
)


def _make_doc(*, deleted_at: datetime | None = None) -> Document:
    now = datetime.now(tz=UTC)
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        type_code="contract",
        number="TEST-001",
        issue_date=None,
        expiry_date=None,
        responsible_user_id=None,
        status="archived" if deleted_at else "active",
        notes=None,
        created_by=uuid.uuid4(),
        updated_by=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
    )


# ---------------------------------------------------------------------------
# ArchiveDocument
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_active_document() -> None:
    doc = _make_doc()
    repo = FakeDocumentRepository([doc])
    outbox = FakeEventOutbox()
    use_case = ArchiveDocument(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    await use_case.execute(
        document_id=doc.id,
        actor_id=uuid.uuid4(),
    )

    stored = await repo.get_by_id(doc.id)
    assert stored is not None
    assert stored.deleted_at is not None
    assert stored.status == "archived"

    assert len(outbox.published) == 1
    env, topic = outbox.published[0]
    assert env.type == "registry.document.archived.v1"


@pytest.mark.asyncio
async def test_archive_already_archived_is_idempotent() -> None:
    """Second archive call: no state change, no outbox event (US-6 AC)."""
    doc = _make_doc(deleted_at=datetime.now(tz=UTC))
    repo = FakeDocumentRepository([doc])
    outbox = FakeEventOutbox()
    use_case = ArchiveDocument(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    original_deleted_at = doc.deleted_at
    await use_case.execute(document_id=doc.id, actor_id=uuid.uuid4())

    stored = await repo.get_by_id(doc.id)
    assert stored is not None
    assert stored.deleted_at == original_deleted_at  # unchanged
    assert len(outbox.published) == 0  # no duplicate event


@pytest.mark.asyncio
async def test_archive_nonexistent_raises() -> None:
    repo = FakeDocumentRepository()
    use_case = ArchiveDocument(
        repo=repo,
        outbox=FakeEventOutbox(),
        clock=FakeClock(),  # type: ignore[arg-type]
    )

    with pytest.raises(DocumentNotFoundError):
        await use_case.execute(document_id=uuid.uuid4(), actor_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# RestoreDocument
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_archived_document() -> None:
    doc = _make_doc(deleted_at=datetime.now(tz=UTC))
    repo = FakeDocumentRepository([doc])
    outbox = FakeEventOutbox()
    use_case = RestoreDocument(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    await use_case.execute(document_id=doc.id, actor_id=uuid.uuid4())

    stored = await repo.get_by_id(doc.id)
    assert stored is not None
    assert stored.deleted_at is None
    assert stored.status == "active"

    assert len(outbox.published) == 1
    env, _ = outbox.published[0]
    assert env.type == "registry.document.restored.v1"


@pytest.mark.asyncio
async def test_restore_active_document_is_idempotent() -> None:
    """Restoring already-active doc: no state change, no outbox event (US-7 AC)."""
    doc = _make_doc()  # deleted_at=None
    repo = FakeDocumentRepository([doc])
    outbox = FakeEventOutbox()
    use_case = RestoreDocument(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    await use_case.execute(document_id=doc.id, actor_id=uuid.uuid4())

    assert len(outbox.published) == 0
