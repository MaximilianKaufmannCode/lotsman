# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ImportXlsxConfirm use case."""

from __future__ import annotations

import gzip
import pickle
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from registry_service.application.dto import ImportConfirmCommand, ImportDecision
from registry_service.application.use_cases.import_xlsx_confirm import ImportXlsxConfirm
from registry_service.domain.custom_fields import CustomField, FieldType
from registry_service.domain.entities import Asset, DocumentType
from registry_service.domain.errors import ImportSessionNotFoundError, RequiredFieldMissingError
from tests.unit.use_cases.fakes import (
    FakeAssetRepository,
    FakeClock,
    FakeDocumentRepository,
    FakeDocumentTypeRepository,
    FakeEventOutbox,
)

_NOW = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)


def _make_doc_type(code: str = "license", schema: list[CustomField] | None = None) -> DocumentType:
    return DocumentType(
        code=code,
        display_name=code.capitalize(),
        pre_notice_days=[30],
        notify_in_day=True,
        overdue_every_days=7,
        custom_field_schema=schema or [],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_session_data(
    headers: list[tuple[int, str]],
    rows: list[tuple[Any, ...]],
    known_col_map: list[tuple[str, str]] | None = None,
    unknown_headers: list[str] | None = None,
) -> bytes:
    data = {
        "headers": headers,
        "rows": rows,
        "known_columns": known_col_map or [],
        "unknown_headers": unknown_headers or [],
    }
    return gzip.compress(pickle.dumps(data))


def _fake_redis_store(session_id: str, data: bytes) -> MagicMock:
    """Build a fake redis context manager that returns session data for the given ID."""
    store: dict[str, bytes] = {f"import:session:{session_id}": data}

    class FakeRedisConn:
        async def get(self, key: str) -> bytes | None:
            return store.get(key)

        async def delete(self, key: str) -> None:
            store.pop(key, None)

        async def set(self, key: str, value: bytes, ex: int = 0) -> None:
            store[key] = value

        async def __aenter__(self) -> FakeRedisConn:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    mock = MagicMock()
    mock.return_value = FakeRedisConn()
    return mock


class TestImportXlsxConfirmHappyPath:
    @pytest.mark.asyncio
    async def test_known_columns_only_imports_documents(self) -> None:
        """All standard headers → no decisions needed → documents inserted."""
        session_id = str(uuid.uuid4())
        dt = _make_doc_type("license")
        asset = Asset.create(name="ООО Ромашка")

        # Session: 2 columns (asset, type_code), 1 data row
        session_data = _make_session_data(
            headers=[(0, "Контрагент"), (1, "Тип")],
            rows=[("ООО Ромашка", "license")],
            known_col_map=[("Контрагент", "asset"), ("Тип", "type_code")],
            unknown_headers=[],
        )

        doc_repo = FakeDocumentRepository()
        asset_repo = FakeAssetRepository(assets=[asset])
        type_repo = FakeDocumentTypeRepository(types=[dt])
        outbox = FakeEventOutbox()

        use_case = ImportXlsxConfirm(
            doc_repo=doc_repo,
            asset_repo=asset_repo,
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", _fake_redis_store(session_id, session_data)):
            result = await use_case.execute(
                cmd=ImportConfirmCommand(
                    import_session_id=session_id,
                    decisions=[],
                    actor_id=uuid.uuid4(),
                )
            )

        assert result.rows_imported == 1
        assert result.rows_failed == 0
        assert len(doc_repo._store) == 1

    @pytest.mark.asyncio
    async def test_create_new_adds_field_to_schema(self) -> None:
        session_id = str(uuid.uuid4())
        dt = _make_doc_type("license")
        asset = Asset.create(name="Компания А")

        session_data = _make_session_data(
            headers=[(0, "Контрагент"), (1, "Тип"), (2, "НовоеПоле")],
            rows=[("Компания А", "license", "some value")],
            known_col_map=[("Контрагент", "asset"), ("Тип", "type_code")],
            unknown_headers=["НовоеПоле"],
        )

        doc_repo = FakeDocumentRepository()
        asset_repo = FakeAssetRepository(assets=[asset])
        type_repo = FakeDocumentTypeRepository(types=[dt])
        outbox = FakeEventOutbox()

        use_case = ImportXlsxConfirm(
            doc_repo=doc_repo,
            asset_repo=asset_repo,
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", _fake_redis_store(session_id, session_data)):
            result = await use_case.execute(
                cmd=ImportConfirmCommand(
                    import_session_id=session_id,
                    decisions=[
                        ImportDecision(
                            header="НовоеПоле",
                            action="create_new",
                            new_key="novoe_pole",
                            target_type="license",
                            field_type="text",
                            display_name="Новое Поле",
                        )
                    ],
                    actor_id=uuid.uuid4(),
                )
            )

        assert result.rows_imported == 1
        assert {"type_code": "license", "field_key": "novoe_pole"} in result.fields_added
        # The new field should now be in the type's schema
        updated_dt = await type_repo.get_by_code("license")
        assert updated_dt is not None
        assert any(f.key == "novoe_pole" for f in updated_dt.custom_field_schema)

    @pytest.mark.asyncio
    async def test_skip_action_drops_column(self) -> None:
        """Columns with action=skip are excluded from documents."""
        session_id = str(uuid.uuid4())
        dt = _make_doc_type("license")
        asset = Asset.create(name="Компания Б")

        session_data = _make_session_data(
            headers=[(0, "Контрагент"), (1, "Тип"), (2, "Игнорируемое")],
            rows=[("Компания Б", "license", "ignored_value")],
            known_col_map=[("Контрагент", "asset"), ("Тип", "type_code")],
            unknown_headers=["Игнорируемое"],
        )

        doc_repo = FakeDocumentRepository()
        asset_repo = FakeAssetRepository(assets=[asset])
        type_repo = FakeDocumentTypeRepository(types=[dt])
        outbox = FakeEventOutbox()

        use_case = ImportXlsxConfirm(
            doc_repo=doc_repo,
            asset_repo=asset_repo,
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", _fake_redis_store(session_id, session_data)):
            result = await use_case.execute(
                cmd=ImportConfirmCommand(
                    import_session_id=session_id,
                    decisions=[
                        ImportDecision(
                            header="Игнорируемое",
                            action="skip",
                        )
                    ],
                    actor_id=uuid.uuid4(),
                )
            )

        assert result.rows_imported == 1
        docs = list(doc_repo._store.values())
        assert len(docs) == 1
        # custom_field_values should not contain anything from the skipped column
        assert "игнорируемое" not in docs[0].custom_field_values

    @pytest.mark.asyncio
    async def test_map_to_existing_reuses_field(self) -> None:
        existing_field = CustomField(
            key="existing_key", display_name="Existing", type=FieldType.TEXT
        )
        dt = _make_doc_type("license", schema=[existing_field])
        asset = Asset.create(name="Компания В")
        session_id = str(uuid.uuid4())

        session_data = _make_session_data(
            headers=[(0, "Контрагент"), (1, "Тип"), (2, "АльтернативноеНазвание")],
            rows=[("Компания В", "license", "mapped_value")],
            known_col_map=[("Контрагент", "asset"), ("Тип", "type_code")],
            unknown_headers=["АльтернативноеНазвание"],
        )

        doc_repo = FakeDocumentRepository()
        asset_repo = FakeAssetRepository(assets=[asset])
        type_repo = FakeDocumentTypeRepository(types=[dt])
        outbox = FakeEventOutbox()

        use_case = ImportXlsxConfirm(
            doc_repo=doc_repo,
            asset_repo=asset_repo,
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", _fake_redis_store(session_id, session_data)):
            result = await use_case.execute(
                cmd=ImportConfirmCommand(
                    import_session_id=session_id,
                    decisions=[
                        ImportDecision(
                            header="АльтернативноеНазвание",
                            action="map_to_existing",
                            target_type="license",
                            mapped_to_field="existing_key",
                        )
                    ],
                    actor_id=uuid.uuid4(),
                )
            )

        assert result.rows_imported == 1
        # No new fields added since we mapped to existing
        assert result.fields_added == []
        docs = list(doc_repo._store.values())
        assert docs[0].custom_field_values.get("existing_key") == "mapped_value"

    @pytest.mark.asyncio
    async def test_audit_event_emitted(self) -> None:
        session_id = str(uuid.uuid4())
        dt = _make_doc_type("license")
        asset = Asset.create(name="Аудит Компания")

        session_data = _make_session_data(
            headers=[(0, "Контрагент"), (1, "Тип")],
            rows=[("Аудит Компания", "license")],
            known_col_map=[("Контрагент", "asset"), ("Тип", "type_code")],
            unknown_headers=[],
        )

        doc_repo = FakeDocumentRepository()
        asset_repo = FakeAssetRepository(assets=[asset])
        type_repo = FakeDocumentTypeRepository(types=[dt])
        outbox = FakeEventOutbox()

        use_case = ImportXlsxConfirm(
            doc_repo=doc_repo,
            asset_repo=asset_repo,
            type_repo=type_repo,
            outbox=outbox,
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with patch("redis.asyncio.from_url", _fake_redis_store(session_id, session_data)):
            await use_case.execute(
                cmd=ImportConfirmCommand(
                    import_session_id=session_id,
                    decisions=[],
                    actor_id=uuid.uuid4(),
                )
            )

        import_events = [(e, t) for (e, t) in outbox.published if t == "registry.imports"]
        assert any(e.type == "registry.import.completed.v1" for e, _ in import_events)


class TestImportXlsxConfirmErrors:
    @pytest.mark.asyncio
    async def test_session_not_found_raises(self) -> None:
        session_id = str(uuid.uuid4())

        class EmptyRedis:
            async def get(self, key: str) -> None:
                return None

            async def __aenter__(self) -> EmptyRedis:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        mock = MagicMock()
        mock.return_value = EmptyRedis()

        use_case = ImportXlsxConfirm(
            doc_repo=FakeDocumentRepository(),
            asset_repo=FakeAssetRepository(),
            type_repo=FakeDocumentTypeRepository(),
            outbox=FakeEventOutbox(),
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with (
            patch("redis.asyncio.from_url", mock),
            pytest.raises(ImportSessionNotFoundError),
        ):
            await use_case.execute(
                cmd=ImportConfirmCommand(
                    import_session_id=session_id,
                    decisions=[],
                    actor_id=uuid.uuid4(),
                )
            )

    @pytest.mark.asyncio
    async def test_missing_decision_raises(self) -> None:
        """If an unknown header has no decision, raise RequiredFieldMissingError."""
        session_id = str(uuid.uuid4())
        session_data = _make_session_data(
            headers=[(0, "Контрагент"), (1, "НеизвестноеПоле")],
            rows=[("А", "x")],
            known_col_map=[("Контрагент", "asset")],
            unknown_headers=["НеизвестноеПоле"],
        )

        use_case = ImportXlsxConfirm(
            doc_repo=FakeDocumentRepository(),
            asset_repo=FakeAssetRepository(),
            type_repo=FakeDocumentTypeRepository(),
            outbox=FakeEventOutbox(),
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with (
            patch("redis.asyncio.from_url", _fake_redis_store(session_id, session_data)),
            pytest.raises(RequiredFieldMissingError, match="НеизвестноеПоле"),
        ):
            await use_case.execute(
                cmd=ImportConfirmCommand(
                    import_session_id=session_id,
                    decisions=[],  # No decision for unknown header
                    actor_id=uuid.uuid4(),
                )
            )

    @pytest.mark.asyncio
    async def test_decision_for_unknown_header_raises(self) -> None:
        """Providing a decision for a header that was NOT in the unknown list → error."""
        session_id = str(uuid.uuid4())
        session_data = _make_session_data(
            headers=[(0, "Контрагент")],
            rows=[("А",)],
            known_col_map=[("Контрагент", "asset")],
            unknown_headers=[],  # No unknowns
        )

        use_case = ImportXlsxConfirm(
            doc_repo=FakeDocumentRepository(),
            asset_repo=FakeAssetRepository(),
            type_repo=FakeDocumentTypeRepository(),
            outbox=FakeEventOutbox(),
            clock=FakeClock(),
            redis_url="redis://fake",
        )

        with (
            patch("redis.asyncio.from_url", _fake_redis_store(session_id, session_data)),
            pytest.raises(RequiredFieldMissingError),
        ):
            await use_case.execute(
                cmd=ImportConfirmCommand(
                    import_session_id=session_id,
                    decisions=[
                        ImportDecision(
                            header="НесуществующаяКолонка",
                            action="skip",
                        )
                    ],
                    actor_id=uuid.uuid4(),
                )
            )
