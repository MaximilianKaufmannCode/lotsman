# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ImportXlsxPreview use case.

Uses a fake xlsx built in-memory with openpyxl to test header classification.
Redis calls are monkey-patched.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from registry_service.application.use_cases.import_xlsx_preview import ImportXlsxPreview
from registry_service.domain.custom_fields import CustomField, FieldType
from registry_service.domain.entities import DocumentType
from tests.unit.use_cases.fakes import FakeClock, FakeDocumentTypeRepository, FakeEventOutbox


def _build_xlsx(headers: list[str], rows: list[list[Any]]) -> bytes:
    """Build an in-memory xlsx with openpyxl."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(headers)  # type: ignore[union-attr]
    for row in rows:
        ws.append(row)  # type: ignore[union-attr]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_doc_type(code: str, schema: list[CustomField]) -> DocumentType:
    now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    return DocumentType(
        code=code,
        display_name=code.capitalize(),
        pre_notice_days=[30],
        notify_in_day=True,
        overdue_every_days=7,
        custom_field_schema=schema,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def fake_redis() -> MagicMock:
    """Patch redis.asyncio.from_url to return an in-memory store."""
    store: dict[str, Any] = {}

    class FakeRedisConn:
        async def set(self, key: str, value: bytes, ex: int = 0) -> None:
            store[key] = value

        async def get(self, key: str) -> bytes | None:
            return store.get(key)

        async def delete(self, key: str) -> None:
            store.pop(key, None)

        async def __aenter__(self) -> FakeRedisConn:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    mock = MagicMock()
    mock.return_value = FakeRedisConn()
    return mock


class TestImportXlsxPreviewClassification:
    @pytest.mark.asyncio
    async def test_standard_headers_classified(self, fake_redis: MagicMock) -> None:
        file_bytes = _build_xlsx(
            ["Контрагент", "Тип", "№ документа", "Действ. до"],
            [["ООО Ромашка", "license", "123", "2025-12-31"]],
        )
        type_repo = FakeDocumentTypeRepository()
        outbox = FakeEventOutbox()
        use_case = ImportXlsxPreview(
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", fake_redis):
            dto = await use_case.execute(
                actor_id=uuid.uuid4(),
                file_bytes=file_bytes,
            )

        assert dto.rows_total == 1
        assert len(dto.unknown_columns) == 0
        known_headers = {c.header: c.matched_to for c in dto.known_columns}
        assert known_headers["Контрагент"] == "asset"
        assert known_headers["Тип"] == "type_code"
        assert known_headers["№ документа"] == "doc_number"
        assert known_headers["Действ. до"] == "expires_at"

    @pytest.mark.asyncio
    async def test_known_custom_field_classified(self, fake_redis: MagicMock) -> None:
        custom_field = CustomField(
            key="issuer_name",
            display_name="Наименование лицензиара",
            type=FieldType.TEXT,
        )
        dt = _make_doc_type("license", schema=[custom_field])
        type_repo = FakeDocumentTypeRepository(types=[dt])
        outbox = FakeEventOutbox()

        file_bytes = _build_xlsx(
            ["Контрагент", "Наименование лицензиара"],
            [["ООО Ромашка", "ФНС России"]],
        )
        use_case = ImportXlsxPreview(
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", fake_redis):
            dto = await use_case.execute(actor_id=uuid.uuid4(), file_bytes=file_bytes)

        custom_matches = [c for c in dto.known_columns if c.matched_to.startswith("custom:")]
        assert len(custom_matches) == 1
        assert custom_matches[0].matched_to == "custom:license:issuer_name"

    @pytest.mark.asyncio
    async def test_unknown_headers_detected(self, fake_redis: MagicMock) -> None:
        file_bytes = _build_xlsx(
            ["Контрагент", "МойНеизвестныйСтолбец"],
            [["ООО Ромашка", "some value"]],
        )
        type_repo = FakeDocumentTypeRepository()
        outbox = FakeEventOutbox()
        use_case = ImportXlsxPreview(
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", fake_redis):
            dto = await use_case.execute(actor_id=uuid.uuid4(), file_bytes=file_bytes)

        assert len(dto.unknown_columns) == 1
        assert dto.unknown_columns[0].header == "МойНеизвестныйСтолбец"
        assert dto.unknown_columns[0].matched_to == "unknown"

    @pytest.mark.asyncio
    async def test_suggested_type_date(self, fake_redis: MagicMock) -> None:
        file_bytes = _build_xlsx(
            ["Контрагент", "Дата события"],
            [
                ["А", "2025-01-01"],
                ["Б", "2025-06-15"],
                ["В", "2024-12-31"],
            ],
        )
        type_repo = FakeDocumentTypeRepository()
        outbox = FakeEventOutbox()
        use_case = ImportXlsxPreview(
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", fake_redis):
            dto = await use_case.execute(actor_id=uuid.uuid4(), file_bytes=file_bytes)

        unknown = dto.unknown_columns[0]
        assert unknown.suggested_type == "date"

    @pytest.mark.asyncio
    async def test_suggested_type_number(self, fake_redis: MagicMock) -> None:
        file_bytes = _build_xlsx(
            ["Контрагент", "Сумма"],
            [
                ["А", 1000],
                ["Б", 2500],
                ["В", 750],
            ],
        )
        type_repo = FakeDocumentTypeRepository()
        outbox = FakeEventOutbox()
        use_case = ImportXlsxPreview(
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", fake_redis):
            dto = await use_case.execute(actor_id=uuid.uuid4(), file_bytes=file_bytes)

        unknown = dto.unknown_columns[0]
        assert unknown.suggested_type == "number"

    @pytest.mark.asyncio
    async def test_suggested_type_text_fallback(self, fake_redis: MagicMock) -> None:
        file_bytes = _build_xlsx(
            ["Контрагент", "Произвольное поле"],
            [
                ["А", "abc"],
                ["Б", "def"],
            ],
        )
        type_repo = FakeDocumentTypeRepository()
        outbox = FakeEventOutbox()
        use_case = ImportXlsxPreview(
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", fake_redis):
            dto = await use_case.execute(actor_id=uuid.uuid4(), file_bytes=file_bytes)

        unknown = dto.unknown_columns[0]
        assert unknown.suggested_type == "text"

    @pytest.mark.asyncio
    async def test_audit_event_emitted(self, fake_redis: MagicMock) -> None:
        file_bytes = _build_xlsx(
            ["Контрагент", "НеизвестнаяКолонка"],
            [["А", "val"]],
        )
        type_repo = FakeDocumentTypeRepository()
        outbox = FakeEventOutbox()
        use_case = ImportXlsxPreview(
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", fake_redis):
            await use_case.execute(actor_id=uuid.uuid4(), file_bytes=file_bytes)

        assert len(outbox.published) == 1
        envelope, topic = outbox.published[0]
        assert topic == "registry.imports"
        assert envelope.type == "registry.import.preview.v1"
        assert envelope.payload["unknown_count"] == 1

    @pytest.mark.asyncio
    async def test_session_id_returned(self, fake_redis: MagicMock) -> None:
        file_bytes = _build_xlsx(["Контрагент"], [["А"]])
        type_repo = FakeDocumentTypeRepository()
        outbox = FakeEventOutbox()
        use_case = ImportXlsxPreview(
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", fake_redis):
            dto = await use_case.execute(actor_id=uuid.uuid4(), file_bytes=file_bytes)

        assert dto.import_session_id
        # Should be a valid UUID
        uuid.UUID(dto.import_session_id)
