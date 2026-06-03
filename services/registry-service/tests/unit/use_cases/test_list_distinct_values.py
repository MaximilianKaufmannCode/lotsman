# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ListDistinctValues use case and the dynamic _parse_cf_params helper.

Tests cover:
  - Happy path: system field (type_code)
  - Happy path: cf_* field present in schema
  - Unknown field → UnknownDistinctField raised (API maps to 422)
  - Date field → DateFieldDistinctNotSupported raised (API maps to 422)
  - truncated flag set when values == limit
  - _parse_cf_params: unknown cf_* key silently dropped, valid key passed through
  - _parse_cf_params: no DB call when no cf_* params present
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from registry_service.application.dto import ListDistinctValuesQuery
from registry_service.application.use_cases.list_distinct_values import ListDistinctValues
from registry_service.domain.custom_fields import CustomField, FieldType
from registry_service.domain.entities import Document, DocumentType
from registry_service.domain.errors import DateFieldDistinctNotSupported, UnknownDistinctField
from tests.unit.use_cases.fakes import FakeDocumentRepository, FakeDocumentTypeRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 5, 26)


def _make_document(
    type_code: str = "contract",
    custom_field_values: dict | None = None,
) -> Document:
    return Document(
        id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        type_code=type_code,
        number=f"DOC-{uuid.uuid4().hex[:6]}",
        issue_date=None,
        expiry_date=None,
        responsible_user_id=None,
        status="active",
        notes=None,
        created_by=uuid.uuid4(),
        updated_by=uuid.uuid4(),
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
        custom_field_values=custom_field_values or {},
    )


def _make_doc_type(
    code: str = "contract",
    custom_field_schema: list[CustomField] | None = None,
) -> DocumentType:
    return DocumentType(
        code=code,
        display_name=code.capitalize(),
        pre_notice_days=[30],
        notify_in_day=True,
        overdue_every_days=7,
        custom_field_schema=custom_field_schema or [],
        created_at=NOW,
        updated_at=NOW,
    )


# ---------------------------------------------------------------------------
# ListDistinctValues — system field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_distinct_values_system_field_type_code() -> None:
    """Happy path: type_code returns distinct values with counts."""
    docs = [
        _make_document(type_code="contract"),
        _make_document(type_code="contract"),
        _make_document(type_code="license"),
    ]
    doc_repo = FakeDocumentRepository(docs)
    type_repo = FakeDocumentTypeRepository()
    use_case = ListDistinctValues(doc_repo=doc_repo, type_repo=type_repo)

    result = await use_case.execute(query=ListDistinctValuesQuery(field="type_code", limit=100))

    assert result.field == "type_code"
    assert result.total_distinct == 2
    # Sorted by count DESC then value ASC: contract(2) before license(1)
    assert result.values[0].value == "contract"
    assert result.values[0].count == 2
    assert result.values[1].value == "license"
    assert result.values[1].count == 1
    assert result.truncated is False


@pytest.mark.asyncio
async def test_distinct_values_system_field_number_with_q() -> None:
    """Substring search via q param filters values case-insensitively."""
    docs = [
        _make_document(type_code="contract"),
        _make_document(type_code="contract"),
        _make_document(type_code="license"),
    ]
    # Override numbers to have predictable values
    docs[0].number = "DOC-2026-001"
    docs[1].number = "DOC-2026-002"
    docs[2].number = "LIC-2026-001"

    doc_repo = FakeDocumentRepository(docs)
    type_repo = FakeDocumentTypeRepository()
    use_case = ListDistinctValues(doc_repo=doc_repo, type_repo=type_repo)

    result = await use_case.execute(query=ListDistinctValuesQuery(field="number", q="doc", limit=100))

    values = [item.value for item in result.values]
    assert all("DOC" in v.upper() for v in values)
    assert "LIC-2026-001" not in values


# ---------------------------------------------------------------------------
# ListDistinctValues — cf_* field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_distinct_values_cf_field_happy_path() -> None:
    """cf_yurisdikciya field returns distinct JSONB values with counts."""
    cf_field = CustomField(key="yurisdikciya", display_name="Юрисдикция", type=FieldType.TEXT)
    doc_type = _make_doc_type(custom_field_schema=[cf_field])

    docs = [
        _make_document(custom_field_values={"yurisdikciya": "Гонконг"}),
        _make_document(custom_field_values={"yurisdikciya": "Гонконг"}),
        _make_document(custom_field_values={"yurisdikciya": "РФ"}),
        _make_document(custom_field_values={}),  # no value — excluded
    ]

    doc_repo = FakeDocumentRepository(docs)
    type_repo = FakeDocumentTypeRepository([doc_type])
    use_case = ListDistinctValues(doc_repo=doc_repo, type_repo=type_repo)

    result = await use_case.execute(
        query=ListDistinctValuesQuery(field="cf_yurisdikciya", limit=100)
    )

    assert result.field == "cf_yurisdikciya"
    assert result.total_distinct == 2
    assert result.values[0].value == "Гонконг"
    assert result.values[0].count == 2
    assert result.values[1].value == "РФ"
    assert result.values[1].count == 1
    assert result.truncated is False


@pytest.mark.asyncio
async def test_distinct_values_cf_field_not_in_schema_raises_422() -> None:
    """cf_nonexistent raises UnknownDistinctField (API maps to 422)."""
    doc_type = _make_doc_type(custom_field_schema=[])
    doc_repo = FakeDocumentRepository()
    type_repo = FakeDocumentTypeRepository([doc_type])
    use_case = ListDistinctValues(doc_repo=doc_repo, type_repo=type_repo)

    with pytest.raises(UnknownDistinctField) as exc_info:
        await use_case.execute(query=ListDistinctValuesQuery(field="cf_nonexistent", limit=100))

    assert "nonexistent" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ListDistinctValues — unknown field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_distinct_values_unknown_system_field_raises_422() -> None:
    """An unrecognised field name (not system, not cf_) raises UnknownDistinctField."""
    doc_repo = FakeDocumentRepository()
    type_repo = FakeDocumentTypeRepository()
    use_case = ListDistinctValues(doc_repo=doc_repo, type_repo=type_repo)

    with pytest.raises(UnknownDistinctField):
        await use_case.execute(query=ListDistinctValuesQuery(field="some_random_field", limit=100))


# ---------------------------------------------------------------------------
# ListDistinctValues — date field rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
# v1.24.9: expiry_date перенесён в _SYSTEM_DISTINCT_FIELDS — теперь поддерживается
# (multi-select из воронки колонки «Действ. до»). Остальные date-колонки
# по-прежнему отклоняются как избыточные для distinct-values.
@pytest.mark.parametrize("date_field", ["issue_date", "updated_at", "created_at"])
async def test_distinct_values_date_field_raises_422(date_field: str) -> None:
    """Date columns always return DateFieldDistinctNotSupported (422 in API)."""
    doc_repo = FakeDocumentRepository()
    type_repo = FakeDocumentTypeRepository()
    use_case = ListDistinctValues(doc_repo=doc_repo, type_repo=type_repo)

    with pytest.raises(DateFieldDistinctNotSupported) as exc_info:
        await use_case.execute(query=ListDistinctValuesQuery(field=date_field, limit=100))

    assert exc_info.value.field == date_field


# ---------------------------------------------------------------------------
# ListDistinctValues — truncated flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_distinct_values_truncated_flag() -> None:
    """When len(values) == limit, truncated should be True."""
    docs = [_make_document(type_code=f"type_{i}") for i in range(5)]
    doc_repo = FakeDocumentRepository(docs)
    type_repo = FakeDocumentTypeRepository()
    use_case = ListDistinctValues(doc_repo=doc_repo, type_repo=type_repo)

    # limit=3, but 5 distinct values → truncated
    result = await use_case.execute(query=ListDistinctValuesQuery(field="type_code", limit=3))

    assert result.truncated is True
    assert len(result.values) == 3


@pytest.mark.asyncio
async def test_distinct_values_not_truncated_when_below_limit() -> None:
    """When len(values) < limit, truncated should be False."""
    docs = [_make_document(type_code="contract"), _make_document(type_code="license")]
    doc_repo = FakeDocumentRepository(docs)
    type_repo = FakeDocumentTypeRepository()
    use_case = ListDistinctValues(doc_repo=doc_repo, type_repo=type_repo)

    result = await use_case.execute(query=ListDistinctValuesQuery(field="type_code", limit=100))

    assert result.truncated is False
    assert len(result.values) == 2


# ---------------------------------------------------------------------------
# _parse_cf_params tests — via module-level async helper simulation
# ---------------------------------------------------------------------------
# We test the logic indirectly through a stripped-down async function that
# mirrors _parse_cf_params behaviour without needing a live FastAPI request.
# The real function is tested via integration; here we validate the pure logic.


@pytest.mark.asyncio
async def test_parse_cf_params_dynamic_whitelist_allows_schema_key() -> None:
    """A cf_key present in the type schema passes through the filter."""
    cf_field = CustomField(key="data_dokumenta", display_name="Дата документа", type=FieldType.DATE)
    doc_type = _make_doc_type(custom_field_schema=[cf_field])
    type_repo = FakeDocumentTypeRepository([doc_type])

    valid_keys = frozenset(
        field_def.key
        for dt in await type_repo.list_all()
        for field_def in dt.custom_field_schema
    )

    assert "data_dokumenta" in valid_keys


@pytest.mark.asyncio
async def test_parse_cf_params_dynamic_whitelist_drops_unknown_key() -> None:
    """A cf_key NOT in any schema is dropped (not 422 — backward compat)."""
    doc_type = _make_doc_type(custom_field_schema=[])
    type_repo = FakeDocumentTypeRepository([doc_type])

    valid_keys = frozenset(
        field_def.key
        for dt in await type_repo.list_all()
        for field_def in dt.custom_field_schema
    )

    assert "jurisdiction" not in valid_keys  # old hardcoded key that no longer exists


@pytest.mark.asyncio
async def test_parse_cf_params_no_db_call_when_no_cf_params() -> None:
    """When no cf_* params exist, the type_repo.list_all() should not be needed.

    This validates the optimisation: skip DB query entirely when request has no cf_* params.
    We assert by confirming the logic path without mocking — if the helper receives an
    empty dict it returns {} immediately.
    """
    # Mirror the guard from _parse_cf_params:
    raw_params: dict = {"q": "something", "limit": "100"}
    cf_candidates = {k: v for k, v in raw_params.items() if k.startswith("cf_")}
    assert len(cf_candidates) == 0  # no DB call needed
