# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for BulkArchiveDocuments use case (US-23)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from registry_service.application.dto import BulkArchiveCommand
from registry_service.application.use_cases.bulk_archive_documents import BulkArchiveDocuments
from registry_service.domain.entities import Document
from registry_service.domain.errors import BulkLimitExceededError
from tests.unit.use_cases.fakes import FakeClock, FakeDocumentRepository, FakeEventOutbox


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


@pytest.mark.asyncio
async def test_bulk_archive_happy_path() -> None:
    """Archive 15 active documents → archived=15, skipped=0, 1 outbox event."""
    docs = [_make_doc() for _ in range(15)]
    repo = FakeDocumentRepository(docs)
    outbox = FakeEventOutbox()
    use_case = BulkArchiveDocuments(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(
        cmd=BulkArchiveCommand(
            ids=[d.id for d in docs],
            actor_id=uuid.uuid4(),
        )
    )

    assert result.archived == 15
    assert result.skipped == 0

    # One bulk event emitted
    assert len(outbox.published) == 1
    envelope, topic = outbox.published[0]
    assert envelope.type == "registry.document.bulk_archived.v1"
    assert topic == "registry.documents"


@pytest.mark.asyncio
async def test_bulk_archive_skips_already_archived() -> None:
    """10 IDs submitted, 3 already archived → archived=7, skipped=3."""
    active_docs = [_make_doc() for _ in range(7)]
    archived_docs = [_make_doc(deleted_at=datetime.now(tz=UTC)) for _ in range(3)]
    all_docs = active_docs + archived_docs

    repo = FakeDocumentRepository(all_docs)
    outbox = FakeEventOutbox()
    use_case = BulkArchiveDocuments(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(
        cmd=BulkArchiveCommand(
            ids=[d.id for d in all_docs],
            actor_id=uuid.uuid4(),
        )
    )

    assert result.archived == 7
    assert result.skipped == 3

    # Still one bulk event (for 7 actually-archived docs)
    assert len(outbox.published) == 1


@pytest.mark.asyncio
async def test_bulk_archive_all_already_archived_emits_no_event() -> None:
    """If all submitted IDs are already archived, no outbox event is emitted."""
    docs = [_make_doc(deleted_at=datetime.now(tz=UTC)) for _ in range(5)]
    repo = FakeDocumentRepository(docs)
    outbox = FakeEventOutbox()
    use_case = BulkArchiveDocuments(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(
        cmd=BulkArchiveCommand(
            ids=[d.id for d in docs],
            actor_id=uuid.uuid4(),
        )
    )

    assert result.archived == 0
    assert result.skipped == 5
    assert len(outbox.published) == 0  # no event when nothing was actually archived


@pytest.mark.asyncio
async def test_bulk_archive_exceeds_100_raises() -> None:
    """Submitting >100 IDs raises BulkLimitExceededError (Q3 constraint)."""
    repo = FakeDocumentRepository()
    use_case = BulkArchiveDocuments(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    with pytest.raises(BulkLimitExceededError):
        await use_case.execute(
            cmd=BulkArchiveCommand(
                ids=[uuid.uuid4() for _ in range(101)],
                actor_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_bulk_archive_exactly_100_is_allowed() -> None:
    """100 IDs is exactly at the limit — must not raise."""
    docs = [_make_doc() for _ in range(100)]
    repo = FakeDocumentRepository(docs)
    outbox = FakeEventOutbox()
    use_case = BulkArchiveDocuments(repo=repo, outbox=outbox, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(
        cmd=BulkArchiveCommand(
            ids=[d.id for d in docs],
            actor_id=uuid.uuid4(),
        )
    )

    assert result.archived == 100


@pytest.mark.asyncio
async def test_bulk_archive_documents_marked_as_archived_in_store() -> None:
    """After bulk-archive, the stored documents have deleted_at set."""
    docs = [_make_doc() for _ in range(3)]
    repo = FakeDocumentRepository(docs)
    use_case = BulkArchiveDocuments(repo=repo, outbox=FakeEventOutbox(), clock=FakeClock())  # type: ignore[arg-type]

    await use_case.execute(
        cmd=BulkArchiveCommand(
            ids=[d.id for d in docs],
            actor_id=uuid.uuid4(),
        )
    )

    for doc in docs:
        stored = await repo.get_by_id(doc.id)
        assert stored is not None
        assert stored.deleted_at is not None
        assert stored.status == "archived"
