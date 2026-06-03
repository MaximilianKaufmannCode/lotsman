# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for ListDocuments use case (US-1, US-2, US-3)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from registry_service.application.dto import ListDocumentsQuery
from registry_service.application.use_cases.list_documents import ListDocuments
from registry_service.domain.entities import Document
from tests.unit.use_cases.fakes import FakeClock, FakeDocumentRepository


def _make_doc(
    *,
    asset_id: uuid.UUID | None = None,
    type_code: str = "contract",
    expiry_date: date | None = None,
    deleted_at: datetime | None = None,
    number: str | None = "DOC-001",
) -> Document:
    now = datetime.now(tz=UTC)
    return Document(
        id=uuid.uuid4(),
        asset_id=asset_id or uuid.uuid4(),
        type_code=type_code,
        number=number,
        issue_date=None,
        expiry_date=expiry_date,
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
async def test_list_empty_registry() -> None:
    repo = FakeDocumentRepository()
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery())
    assert result == []


@pytest.mark.asyncio
async def test_list_returns_active_documents() -> None:
    docs = [_make_doc() for _ in range(3)]
    repo = FakeDocumentRepository(docs)
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery())
    assert len(result) == 3


@pytest.mark.asyncio
async def test_list_excludes_archived_by_default() -> None:
    active = _make_doc()
    archived = _make_doc(deleted_at=datetime.now(tz=UTC))
    repo = FakeDocumentRepository([active, archived])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery())
    assert len(result) == 1
    assert result[0].id == active.id


@pytest.mark.asyncio
async def test_list_includes_archived_when_requested() -> None:
    active = _make_doc()
    archived = _make_doc(deleted_at=datetime.now(tz=UTC))
    repo = FakeDocumentRepository([active, archived])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery(include_archived=True))
    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_computes_urgency_status() -> None:
    """Documents have urgency_status computed from expiry_date + today (2026-05-07)."""
    # FakeClock.today() = date(2026, 5, 7)
    # Far-future expiry → ok
    ok_doc = _make_doc(expiry_date=date(2027, 1, 1))
    # Expiry within 30 days → soon
    soon_doc = _make_doc(expiry_date=date(2026, 5, 20))  # 13 days away
    # Already expired → overdue
    overdue_doc = _make_doc(expiry_date=date(2026, 4, 1))  # 36 days ago
    # No expiry → ok
    no_expiry_doc = _make_doc(expiry_date=None)

    repo = FakeDocumentRepository([ok_doc, soon_doc, overdue_doc, no_expiry_doc])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery())
    by_id = {dto.id: dto for dto in result}

    assert by_id[ok_doc.id].urgency_status == "ok"
    assert by_id[soon_doc.id].urgency_status == "soon"
    assert by_id[overdue_doc.id].urgency_status == "overdue"
    assert by_id[no_expiry_doc.id].urgency_status == "ok"


@pytest.mark.asyncio
async def test_list_filters_by_status() -> None:
    """Status filter is applied post-compute (no DB column for urgency)."""
    ok_doc = _make_doc(expiry_date=date(2027, 1, 1))
    soon_doc = _make_doc(expiry_date=date(2026, 5, 20))
    overdue_doc = _make_doc(expiry_date=date(2026, 4, 1))
    repo = FakeDocumentRepository([ok_doc, soon_doc, overdue_doc])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery(status=["soon"]))
    assert len(result) == 1
    assert result[0].id == soon_doc.id


@pytest.mark.asyncio
async def test_list_filters_by_status_multi() -> None:
    """v1.25.5 — urgency status filter accepts multiple values (multi-select)."""
    ok_doc = _make_doc(expiry_date=date(2027, 1, 1))
    soon_doc = _make_doc(expiry_date=date(2026, 5, 20))
    overdue_doc = _make_doc(expiry_date=date(2026, 4, 1))
    repo = FakeDocumentRepository([ok_doc, soon_doc, overdue_doc])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery(status=["soon", "overdue"]))
    ids = {dto.id for dto in result}
    assert ids == {soon_doc.id, overdue_doc.id}
    assert ok_doc.id not in ids


@pytest.mark.asyncio
async def test_list_number_is_null_filter() -> None:
    """v1.25.6 — column-funnel «— Не задано» on № документа returns docs
    with NULL or empty number.
    """
    with_num = _make_doc(number="DOC-001")
    null_num = _make_doc(number=None)
    empty_num = _make_doc(number="")
    repo = FakeDocumentRepository([with_num, null_num, empty_num])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery(number_is_null=True))
    ids = {dto.id for dto in result}
    assert ids == {null_num.id, empty_num.id}
    assert with_num.id not in ids


@pytest.mark.asyncio
async def test_list_filters_by_status_empty_list_is_no_filter() -> None:
    """v1.25.5 — empty status list means no urgency filter (all docs visible)."""
    ok_doc = _make_doc(expiry_date=date(2027, 1, 1))
    soon_doc = _make_doc(expiry_date=date(2026, 5, 20))
    repo = FakeDocumentRepository([ok_doc, soon_doc])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery(status=[]))
    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_archived_doc_urgency_is_archived() -> None:
    """Soft-deleted documents have urgency_status='archived'."""
    archived = _make_doc(deleted_at=datetime.now(tz=UTC))
    repo = FakeDocumentRepository([archived])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery(include_archived=True))
    assert len(result) == 1
    assert result[0].urgency_status == "archived"


@pytest.mark.asyncio
async def test_list_doc_status_archived_overrides_soft_delete_gate() -> None:
    """v1.25.3 — Asking for doc_status=archived must include archived docs.

    Without the override, the repository's `WHERE deleted_at IS NULL` gate
    would kill archived rows (status='archived' implies deleted_at IS NOT
    NULL by domain invariant), making the «Архив» filter return empty —
    the original «архива нет» bug.
    """
    active = _make_doc()
    archived = _make_doc(deleted_at=datetime.now(tz=UTC))
    repo = FakeDocumentRepository([active, archived])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    # include_archived=False (default) but doc_status=["archived"]
    result = await use_case.execute(
        query=ListDocumentsQuery(doc_status=["archived"], include_archived=False)
    )
    assert len(result) == 1
    assert result[0].id == archived.id


@pytest.mark.asyncio
async def test_list_doc_status_both_active_and_archived() -> None:
    """v1.25.3 — doc_status=[active,archived] shows both."""
    active = _make_doc()
    archived = _make_doc(deleted_at=datetime.now(tz=UTC))
    repo = FakeDocumentRepository([active, archived])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(
        query=ListDocumentsQuery(doc_status=["active", "archived"])
    )
    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_doc_status_active_only_keeps_soft_delete_gate() -> None:
    """v1.25.3 — doc_status=[active] does NOT auto-include archived."""
    active = _make_doc()
    archived = _make_doc(deleted_at=datetime.now(tz=UTC))
    repo = FakeDocumentRepository([active, archived])
    use_case = ListDocuments(repo=repo, clock=FakeClock())  # type: ignore[arg-type]

    result = await use_case.execute(query=ListDocumentsQuery(doc_status=["active"]))
    assert len(result) == 1
    assert result[0].id == active.id
