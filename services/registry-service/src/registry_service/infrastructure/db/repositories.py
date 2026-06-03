# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Concrete SQLAlchemy 2.x implementations of repository protocols.

Each repository receives an AsyncSession via dependency injection (api/deps.py).
All write methods must be called within an open transaction managed by the
session context in deps.py (begin_nested or begin()).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

# ORM models live in db/models.py (separate from domain entities)
from registry_service.db import models as m
from registry_service.domain.custom_fields import CustomField
from registry_service.domain.entities import (
    Asset,
    Attachment,
    Document,
    DocumentType,
    ExportJob,
)

# ---------------------------------------------------------------------------
# Mapper helpers — ORM model → domain entity
# ---------------------------------------------------------------------------


def _asset_from_row(row: m.Asset) -> Asset:
    return Asset(
        id=row.id,
        name=row.name,
        inn=row.inn,
        notes=row.notes,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _doc_type_from_row(row: m.DocumentType) -> DocumentType:
    raw_schema = row.custom_field_schema if row.custom_field_schema else []
    custom_schema = [CustomField.from_dict(d) for d in raw_schema]
    return DocumentType(
        code=row.code,
        display_name=row.display_name,
        pre_notice_days=list(row.pre_notice_days),
        notify_in_day=row.notify_in_day,
        overdue_every_days=row.overdue_every_days,
        custom_field_schema=custom_schema,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _document_from_row(row: m.Document) -> Document:
    return Document(
        id=row.id,
        asset_id=row.asset_id,
        type_code=row.type_code,
        number=row.number,
        issue_date=row.issue_date,
        expiry_date=row.expiry_date,
        responsible_user_id=row.responsible_user_id,
        status=row.status,
        notes=row.notes,
        created_by=row.created_by,
        updated_by=row.updated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
        custom_field_values=dict(row.custom_field_values) if row.custom_field_values else {},
    )


def _attachment_from_row(row: m.Attachment) -> Attachment:
    return Attachment(
        id=row.id,
        document_id=row.document_id,
        original_filename=row.original_filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        storage_path=row.storage_path,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _export_job_from_row(row: m.ExportJob) -> ExportJob:
    return ExportJob(
        id=row.id,
        requested_by=row.requested_by,
        status=row.status,
        file_path=row.file_path,
        error=row.error,
        expires_at=row.expires_at,
        filters=row.filters if hasattr(row, "filters") and row.filters else {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# AssetRepository
# ---------------------------------------------------------------------------


class SqlAssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_id(self, asset_id: uuid.UUID) -> Asset | None:
        row = await self._s.get(m.Asset, asset_id)
        return _asset_from_row(row) if row else None

    async def get_active_by_id(self, asset_id: uuid.UUID) -> Asset | None:
        result = await self._s.execute(
            select(m.Asset).where(and_(m.Asset.id == asset_id, m.Asset.deleted_at.is_(None)))
        )
        row = result.scalar_one_or_none()
        return _asset_from_row(row) if row else None

    async def list_active(
        self,
        *,
        q: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Asset]:
        stmt = select(m.Asset).where(m.Asset.deleted_at.is_(None))

        if q:
            if len(q) >= 3:
                # pg_trgm similarity search
                stmt = stmt.where(func.similarity(m.Asset.name, q) > 0.1).order_by(
                    func.similarity(m.Asset.name, q).desc()
                )
            else:
                # ILIKE prefix fallback for short queries (< 3 chars)
                stmt = stmt.where(m.Asset.name.ilike(f"%{q}%"))

        stmt = stmt.order_by(m.Asset.name).offset(offset).limit(limit)
        result = await self._s.execute(stmt)
        return [_asset_from_row(row) for row in result.scalars().all()]

    async def add(self, asset: Asset) -> None:
        row = m.Asset(
            id=asset.id,
            name=asset.name,
            inn=asset.inn,
            notes=asset.notes,
            status=asset.status,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
            deleted_at=asset.deleted_at,
        )
        self._s.add(row)
        await self._s.flush()

    async def update(self, asset: Asset) -> None:
        await self._s.execute(
            update(m.Asset)
            .where(m.Asset.id == asset.id)
            .values(
                name=asset.name,
                inn=asset.inn,
                notes=asset.notes,
                status=asset.status,
                updated_at=asset.updated_at,
                deleted_at=asset.deleted_at,
            )
        )

    async def name_exists_for_active(self, name: str) -> bool:
        result = await self._s.execute(
            select(m.Asset.id).where(and_(m.Asset.name == name, m.Asset.deleted_at.is_(None)))
        )
        return result.scalar_one_or_none() is not None

    async def archive_cascade_documents(
        self,
        asset_id: uuid.UUID,
        now: Any,
    ) -> int:
        result = await self._s.execute(
            update(m.Document)
            .where(
                and_(
                    m.Document.asset_id == asset_id,
                    m.Document.deleted_at.is_(None),
                )
            )
            .values(deleted_at=now, status="archived", updated_at=now)
            .returning(m.Document.id)
        )
        rows = result.fetchall()
        return len(rows)

    async def restore_cascade_documents(
        self,
        asset_id: uuid.UUID,
        now: Any,
    ) -> int:
        """Clear deleted_at for documents that were previously cascade-archived.

        Note: This is currently a no-op placeholder. Restoring cascade-archived
        documents automatically is intentionally deferred — the UI should handle
        per-document restoration to avoid unwanted bulk-restore side effects.
        Returns 0.
        """
        return 0


# ---------------------------------------------------------------------------
# DocumentTypeRepository
# ---------------------------------------------------------------------------


class SqlDocumentTypeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_code(self, code: str) -> DocumentType | None:
        row = await self._s.get(m.DocumentType, code)
        return _doc_type_from_row(row) if row else None

    async def list_all(self) -> list[DocumentType]:
        result = await self._s.execute(select(m.DocumentType).order_by(m.DocumentType.display_name))
        return [_doc_type_from_row(row) for row in result.scalars().all()]

    async def upsert(self, doc_type: DocumentType) -> None:
        schema_json = [f.to_dict() for f in doc_type.custom_field_schema]
        existing = await self._s.get(m.DocumentType, doc_type.code)
        if existing is None:
            row = m.DocumentType(
                code=doc_type.code,
                display_name=doc_type.display_name,
                pre_notice_days=doc_type.pre_notice_days,
                notify_in_day=doc_type.notify_in_day,
                overdue_every_days=doc_type.overdue_every_days,
                custom_field_schema=schema_json,
                created_at=doc_type.created_at,
                updated_at=doc_type.updated_at,
            )
            self._s.add(row)
        else:
            existing.display_name = doc_type.display_name
            existing.pre_notice_days = doc_type.pre_notice_days
            existing.notify_in_day = doc_type.notify_in_day
            existing.overdue_every_days = doc_type.overdue_every_days
            existing.custom_field_schema = schema_json
            existing.updated_at = doc_type.updated_at
        await self._s.flush()

    async def get_by_code_for_update(self, code: str) -> DocumentType | None:
        """Load DocumentType with a SELECT FOR UPDATE lock (serialises concurrent schema edits)."""

        result = await self._s.execute(
            select(m.DocumentType).where(m.DocumentType.code == code).with_for_update()
        )
        row = result.scalar_one_or_none()
        return _doc_type_from_row(row) if row else None

    async def drop_custom_field_from_documents(
        self,
        type_code: str,
        field_key: str,
    ) -> None:
        """Remove field_key from all documents.custom_field_values for the given type_code.

        Uses PostgreSQL's JSONB minus-key operator in a single bulk UPDATE.
        """
        from sqlalchemy import text

        await self._s.execute(
            text(
                "UPDATE registry.documents "
                "SET custom_field_values = custom_field_values - :key "
                "WHERE type_code = :type_code"
            ),
            {"key": field_key, "type_code": type_code},
        )

    async def count_documents_with_field(
        self,
        type_code: str,
        field_key: str,
    ) -> int:
        """Count active documents of `type_code` that have a non-null
        `field_key` in their custom_field_values JSONB. Used by
        UpdateCustomFieldSchema to refuse type changes that would
        invalidate existing data.
        """
        from sqlalchemy import text

        result = await self._s.execute(
            text(
                "SELECT COUNT(*) FROM registry.documents "
                "WHERE type_code = :type_code "
                "  AND deleted_at IS NULL "
                "  AND custom_field_values ? :key"
            ),
            {"key": field_key, "type_code": type_code},
        )
        return int(result.scalar_one())


# ---------------------------------------------------------------------------
# DocumentRepository
# ---------------------------------------------------------------------------

_SORT_COLUMNS: dict[str, Any] = {
    "expiry_date": m.Document.expiry_date,
    "issue_date": m.Document.issue_date,
    "number": m.Document.number,
    "type_code": m.Document.type_code,
    "created_at": m.Document.created_at,
    "updated_at": m.Document.updated_at,
}


class SqlDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        row = await self._s.get(m.Document, document_id)
        return _document_from_row(row) if row else None

    async def list_active(
        self,
        *,
        # legacy single-value params (backward compat — converted to lists internally)
        asset_id: uuid.UUID | None = None,
        type_code: str | None = None,
        # new multi-value params
        asset_ids: list[uuid.UUID] | None = None,
        type_codes: list[str] | None = None,
        responsible_user_ids: list[uuid.UUID] | None = None,
        responsible_is_null: bool | None = None,
        expiry_from: date | None = None,
        expiry_to: date | None = None,
        expiry_is_null: bool | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        doc_status: list[str] | None = None,
        asset_status: list[str] | None = None,
        inn: str | None = None,
        # v1.25.6 — filter by «не задано» for the document number column.
        # SPA воронка отправляет это когда пользователь поставил галочку
        # «— Не задано» в TextFilterPopover для колонки № документа.
        number_is_null: bool | None = None,
        expiry_dates: list[str] | None = None,
        custom_fields: dict[str, str] | None = None,
        custom_field_ranges: dict[str, dict[str, Any]] | None = None,
        q: str | None = None,
        sort: str | None = None,
        dir: str | None = None,
        offset: int = 0,
        limit: int = 100,
        include_archived: bool = False,
    ) -> list[Document]:
        """List documents with multi-criteria filters.

        Backward compat:
          - Legacy `asset_id` (single UUID) is merged into `asset_ids`.
          - Legacy `type_code` (single str) is merged into `type_codes`.

        Filter semantics (all conditions are AND):
          - expiry_is_null=True takes exclusive precedence over expiry_from/expiry_to.
            Passing expiry_is_null=True alongside range params is a caller error;
            validation is done in the use case / API layer before reaching here.
          - custom_fields: dict of {key: value} → each pair emits
            WHERE custom_field_values @> '{"key": "value"}' using GIN jsonb_path_ops index.
          - doc_status: filters documents.status column (active/archived) directly,
            distinct from the include_archived soft-delete gate.
        """
        stmt = select(m.Document)

        # --- Soft-delete gate ---
        if not include_archived:
            stmt = stmt.where(m.Document.deleted_at.is_(None))

        # --- Merge legacy single-value params into multi-value lists ---
        effective_asset_ids: list[uuid.UUID] = list(asset_ids or [])
        if asset_id is not None and asset_id not in effective_asset_ids:
            effective_asset_ids.append(asset_id)

        effective_type_codes: list[str] = list(type_codes or [])
        if type_code is not None and type_code not in effective_type_codes:
            effective_type_codes.append(type_code)

        # --- Multi-select asset filter ---
        if effective_asset_ids:
            if len(effective_asset_ids) == 1:
                stmt = stmt.where(m.Document.asset_id == effective_asset_ids[0])
            else:
                stmt = stmt.where(m.Document.asset_id.in_(effective_asset_ids))

        # --- Multi-select type filter ---
        if effective_type_codes:
            if len(effective_type_codes) == 1:
                stmt = stmt.where(m.Document.type_code == effective_type_codes[0])
            else:
                stmt = stmt.where(m.Document.type_code.in_(effective_type_codes))

        # --- Responsible user filter ---
        if responsible_is_null is True:
            stmt = stmt.where(m.Document.responsible_user_id.is_(None))
        elif responsible_user_ids:
            if len(responsible_user_ids) == 1:
                stmt = stmt.where(m.Document.responsible_user_id == responsible_user_ids[0])
            else:
                stmt = stmt.where(m.Document.responsible_user_id.in_(responsible_user_ids))

        # --- Expiry date filter ---
        # v1.24.9: multi-select из воронки колонки «Действ. до». Поддерживает
        # сентинел "__NULL__" → expiry_date IS NULL (бессрочные). Любые
        # реальные ISO-даты → IN (...). Имеет приоритет НАД expiry_is_null /
        # expiry_from / expiry_to (они остаются для legacy API-callers).
        if expiry_dates:
            from datetime import date as _date

            null_selected = "__NULL__" in expiry_dates
            real_dates: list[_date] = []
            for d in expiry_dates:
                if d == "__NULL__":
                    continue
                try:
                    real_dates.append(_date.fromisoformat(d.strip()))
                except ValueError:
                    continue
            conditions = []
            if real_dates:
                conditions.append(m.Document.expiry_date.in_(real_dates))
            if null_selected:
                conditions.append(m.Document.expiry_date.is_(None))
            if conditions:
                stmt = stmt.where(or_(*conditions) if len(conditions) > 1 else conditions[0])
        elif expiry_is_null is True:
            stmt = stmt.where(m.Document.expiry_date.is_(None))
        else:
            if expiry_from is not None:
                stmt = stmt.where(m.Document.expiry_date >= expiry_from)
            if expiry_to is not None:
                stmt = stmt.where(m.Document.expiry_date <= expiry_to)

        # --- Updated_at range filter ---
        if updated_from is not None:
            stmt = stmt.where(m.Document.updated_at >= updated_from)
        if updated_to is not None:
            stmt = stmt.where(m.Document.updated_at <= updated_to)

        # --- Document status filter (distinct from soft-delete gate) ---
        if doc_status:
            if len(doc_status) == 1:
                stmt = stmt.where(m.Document.status == doc_status[0])
            else:
                stmt = stmt.where(m.Document.status.in_(doc_status))

        # --- Asset (counterparty) status filter — v1.24.1 ---
        # Filter documents by their counterparty's lifecycle state
        # (active / liquidating / archived). Uses subquery on registry.assets.id
        # to keep the main statement composable with the existing asset_ids
        # branch and avoid introducing a JOIN that would change row cardinality.
        if asset_status:
            asset_subq = select(m.Asset.id).where(m.Asset.status.in_(asset_status))
            stmt = stmt.where(m.Document.asset_id.in_(asset_subq))

        # --- Asset INN substring filter — v1.24.2 ---
        # Sidebar "ИНН содержит" — substring match against assets.inn.
        # Subquery to avoid row cardinality changes; ILIKE for case-insensitive
        # contains. Documents without an asset.inn match are excluded.
        if inn and inn.strip():
            inn_pattern = f"%{inn.strip()}%"
            inn_subq = select(m.Asset.id).where(
                m.Asset.inn.is_not(None), m.Asset.inn.ilike(inn_pattern)
            )
            stmt = stmt.where(m.Document.asset_id.in_(inn_subq))

        # --- Custom field containment filter (uses GIN jsonb_path_ops index) ---
        # v1.24.6: sentinel value "__NULL__" filters for documents where the
        # key is missing OR has null/empty value (the «Не задано» option in UI).
        if custom_fields:
            for _key, _val in custom_fields.items():
                if _val == "__NULL__":
                    stmt = stmt.where(
                        or_(
                            ~m.Document.custom_field_values.has_key(_key),  # type: ignore[attr-defined]
                            m.Document.custom_field_values.op("->>")(_key).is_(None),
                            m.Document.custom_field_values.op("->>")(_key) == "",
                        )
                    )
                else:
                    stmt = stmt.where(
                        m.Document.custom_field_values.contains({_key: _val})
                    )

        # --- Custom field DATE range filter — v1.24.17 ---
        # Schema-driven: для любого cf_<key> с типом 'date' в custom_field_schema
        # popover поддерживает from/to/is_null. SQL: (cfv->>'X')::date BETWEEN a AND b.
        # Не использует GIN @>-индекс (cast в date не индексирован) — для текущих
        # объёмов на проде (<100 строк) seq-scan приемлем; expression-index можно
        # добавить per-field позже при росте объёма.
        if custom_field_ranges:
            from datetime import date as _date_cls

            from sqlalchemy import Date as _SADate, cast as _sa_cast

            for _key, _spec in custom_field_ranges.items():
                _is_null = _spec.get("is_null") is True
                _from_str = _spec.get("from")
                _to_str = _spec.get("to")
                if _is_null and not _from_str and not _to_str:
                    # Pure null filter — same shape as __NULL__ above.
                    stmt = stmt.where(
                        or_(
                            ~m.Document.custom_field_values.has_key(_key),  # type: ignore[attr-defined]
                            m.Document.custom_field_values.op("->>")(_key).is_(None),
                            m.Document.custom_field_values.op("->>")(_key) == "",
                        )
                    )
                    continue
                # Range: cast jsonb text → date, compare.
                # Wrap WHERE in NULL-safe: documents без поля не должны падать в range,
                # но и не должны включаться в результат — наоборот, exclude через
                # require has_key (по умолчанию SQL casts NULL→NULL, comparison → NULL → false).
                _val_expr = _sa_cast(
                    m.Document.custom_field_values.op("->>")(_key), _SADate
                )
                # Need key existence to avoid bizarre cast errors on missing key
                stmt = stmt.where(
                    m.Document.custom_field_values.has_key(_key)  # type: ignore[attr-defined]
                )
                if _from_str:
                    try:
                        _from_d = _date_cls.fromisoformat(_from_str.strip())
                    except ValueError:
                        continue
                    stmt = stmt.where(_val_expr >= _from_d)
                if _to_str:
                    try:
                        _to_d = _date_cls.fromisoformat(_to_str.strip())
                    except ValueError:
                        continue
                    stmt = stmt.where(_val_expr <= _to_d)
                # is_null=true combined with from/to is treated as exclusive: range wins.

        # --- «Не задано» filter for № документа (v1.25.6) ---
        # Empty string and NULL both count as «не задано» — registry intake
        # leaves the column blank when documents arrive without a formal
        # number (e.g. некоторые лицензии, отчёты). Distinct-values RPC
        # exposes this as a `null_count`; the column-funnel посылает галку
        # «— Не задано» сюда. Combined with a textual q, both apply (AND).
        if number_is_null:
            stmt = stmt.where(
                or_(m.Document.number.is_(None), m.Document.number == "")
            )

        # --- Full-text / trgm search ---
        if q:
            if len(q) >= 3:
                # pg_trgm on document number; also join-search on asset name via subquery
                stmt = stmt.where(
                    or_(
                        m.Document.number.ilike(f"%{q}%"),
                        m.Document.asset_id.in_(
                            select(m.Asset.id).where(func.similarity(m.Asset.name, q) > 0.1)
                        ),
                    )
                )
            else:
                stmt = stmt.where(m.Document.number.ilike(f"%{q}%"))

        # --- Sorting ---
        sort_col = _SORT_COLUMNS.get(sort or "") if sort else None
        if sort_col is not None:
            if dir and dir.lower() == "desc":
                stmt = stmt.order_by(sort_col.desc().nulls_last(), m.Document.id)
            else:
                stmt = stmt.order_by(sort_col.asc().nulls_last(), m.Document.id)
        else:
            stmt = stmt.order_by(m.Document.created_at.desc())

        stmt = stmt.offset(offset).limit(limit)
        result = await self._s.execute(stmt)
        return [_document_from_row(row) for row in result.scalars().all()]

    async def add(self, document: Document) -> None:
        row = m.Document(
            id=document.id,
            asset_id=document.asset_id,
            type_code=document.type_code,
            number=document.number,
            issue_date=document.issue_date,
            expiry_date=document.expiry_date,
            responsible_user_id=document.responsible_user_id,
            status=document.status,
            notes=document.notes,
            created_by=document.created_by,
            updated_by=document.updated_by,
            created_at=document.created_at,
            updated_at=document.updated_at,
            deleted_at=document.deleted_at,
            custom_field_values=document.custom_field_values,
        )
        self._s.add(row)
        await self._s.flush()

    async def update(self, document: Document) -> None:
        await self._s.execute(
            update(m.Document)
            .where(m.Document.id == document.id)
            .values(
                asset_id=document.asset_id,
                type_code=document.type_code,
                number=document.number,
                issue_date=document.issue_date,
                expiry_date=document.expiry_date,
                responsible_user_id=document.responsible_user_id,
                status=document.status,
                notes=document.notes,
                updated_by=document.updated_by,
                updated_at=document.updated_at,
                deleted_at=document.deleted_at,
                custom_field_values=document.custom_field_values,
            )
        )

    async def bulk_archive(
        self,
        document_ids: list[uuid.UUID],
        now: Any,
    ) -> tuple[int, int]:
        result = await self._s.execute(
            update(m.Document)
            .where(
                and_(
                    m.Document.id.in_(document_ids),
                    m.Document.deleted_at.is_(None),
                )
            )
            .values(deleted_at=now, status="archived", updated_at=now)
            .returning(m.Document.id)
        )
        archived_ids = result.fetchall()
        archived_count = len(archived_ids)
        skipped_count = len(document_ids) - archived_count
        return archived_count, skipped_count

    async def get_with_asset(self, document_id: uuid.UUID) -> Document | None:
        return await self.get_by_id(document_id)

    async def count_distinct_values(
        self,
        *,
        field: str,
        q: str | None = None,
        limit: int = 100,
    ) -> tuple[list[tuple[str, int]], int, int]:
        """Return top-N distinct values + total_distinct + null_count.

        Supported fields: number, asset_name, type_code, expiry_date (v1.24.9).
        `asset_name` is resolved via a subquery join on registry.assets.
        `expiry_date` is cast to ISO-date string for the value.
        """
        from sqlalchemy import distinct, cast, Date, String

        # Map logical field name → ORM column expression.
        # Date columns cast to ISO YYYY-MM-DD for the popover value.
        _system_column_map = {
            "number": m.Document.number,
            "type_code": m.Document.type_code,
            "expiry_date": cast(m.Document.expiry_date, String),  # type: ignore[arg-type]
        }
        # For null_count computation we need the underlying nullable column.
        _null_check_map = {
            "expiry_date": m.Document.expiry_date,
            "number": m.Document.number,
            "type_code": m.Document.type_code,
        }

        if field == "asset_name":
            # Count distinct asset names referenced by non-deleted documents
            base_stmt = (
                select(m.Asset.name.label("value"), func.count().label("cnt"))
                .join(m.Document, m.Document.asset_id == m.Asset.id)
                .where(m.Document.deleted_at.is_(None))
                .where(m.Asset.deleted_at.is_(None))
            )
            if q:
                base_stmt = base_stmt.where(m.Asset.name.ilike(f"%{q}%"))
            base_stmt = (
                base_stmt.group_by(m.Asset.name)
                .order_by(func.count().desc(), m.Asset.name.asc())
            )

            total_stmt = (
                select(func.count(distinct(m.Asset.name)))
                .select_from(m.Asset)
                .join(m.Document, m.Document.asset_id == m.Asset.id)
                .where(m.Document.deleted_at.is_(None))
                .where(m.Asset.deleted_at.is_(None))
            )
            total_result = await self._s.execute(total_stmt)
            total_distinct: int = total_result.scalar_one() or 0

            rows = (await self._s.execute(base_stmt.limit(limit))).all()
            values = [(str(r.value), int(r.cnt)) for r in rows if r.value is not None]
            return values, total_distinct, 0  # asset_name никогда не null

        col = _system_column_map.get(field)
        if col is None:
            # Caller validated field before reaching here; this is a safety net.
            return [], 0, 0
        null_check_col = _null_check_map.get(field, col)

        base_stmt = (
            select(col.label("value"), func.count().label("cnt"))
            .where(m.Document.deleted_at.is_(None))
            .where(null_check_col.isnot(None))
        )
        if q:
            # ILIKE works on text/varchar; expiry_date уже скастован в String.
            base_stmt = base_stmt.where(col.ilike(f"%{q}%"))
        base_stmt = (
            base_stmt.group_by(col)
            .order_by(func.count().desc(), col.asc())
        )

        # Total distinct count (no q/limit)
        total_stmt = (
            select(func.count(distinct(col)))
            .where(m.Document.deleted_at.is_(None))
            .where(null_check_col.isnot(None))
        )
        total_result = await self._s.execute(total_stmt)
        total_distinct = total_result.scalar_one() or 0

        # v1.24.9 — null_count для system-полей (expiry_date может быть NULL).
        null_stmt = (
            select(func.count())
            .select_from(m.Document)
            .where(m.Document.deleted_at.is_(None))
            .where(null_check_col.is_(None))
        )
        null_result = await self._s.execute(null_stmt)
        null_count: int = null_result.scalar_one() or 0

        rows = (await self._s.execute(base_stmt.limit(limit))).all()
        values = [(str(r.value), int(r.cnt)) for r in rows if r.value is not None]
        return values, total_distinct, null_count

    async def count_distinct_cf_values(
        self,
        *,
        cf_key: str,
        q: str | None = None,
        limit: int = 100,
    ) -> tuple[list[tuple[str, int]], int, int]:
        """Return top-N distinct values for a custom-field JSONB key.

        Safety: cf_key is validated against the regex ^[a-z][a-z0-9_]{0,63}$ AND
        whitelisted against document_types.custom_field_schema before this method
        is called. We use SQLAlchemy `op('->>')(key)` to build the JSONB text
        extraction expression — `custom_field_values->>:key` is NOT used directly
        because PostgreSQL does not resolve bind-parameters in the operator chain.
        Instead we use the ORM column `.op('->>')` with a literal string key
        (already validated, no injection possible).

        SQL equivalent:
            SELECT custom_field_values->>'<cf_key>' AS value, COUNT(*) AS cnt
            FROM registry.documents
            WHERE deleted_at IS NULL
              AND custom_field_values ? '<cf_key>'
              AND (<q filter>)
            GROUP BY value
            HAVING value IS NOT NULL
            ORDER BY cnt DESC, value ASC
            LIMIT <limit>
        """
        # Build ->> extraction using ORM operator chain (injection-safe: cf_key is
        # pre-validated by regex + schema whitelist in the use case layer).
        val_expr = m.Document.custom_field_values.op("->>")(cf_key).label("value")

        base_stmt = (
            select(val_expr, func.count().label("cnt"))
            .where(m.Document.deleted_at.is_(None))
            # GIN @> containment check — confirms the key exists in the JSONB object
            .where(m.Document.custom_field_values.has_key(cf_key))  # type: ignore[attr-defined]
        )
        if q:
            base_stmt = base_stmt.where(
                m.Document.custom_field_values.op("->>")(cf_key).ilike(f"%{q}%")
            )
        base_stmt = (
            base_stmt.group_by(val_expr)
            .having(val_expr.isnot(None))
            .order_by(func.count().desc(), val_expr.asc())
        )

        # Total distinct count (no q/limit filter)
        total_stmt = (
            select(func.count(func.distinct(m.Document.custom_field_values.op("->>")(cf_key))))
            .where(m.Document.deleted_at.is_(None))
            .where(m.Document.custom_field_values.has_key(cf_key))  # type: ignore[attr-defined]
            .where(m.Document.custom_field_values.op("->>")(cf_key).isnot(None))
        )
        total_result = await self._s.execute(total_stmt)
        total_distinct: int = total_result.scalar_one() or 0

        rows = (await self._s.execute(base_stmt.limit(limit))).all()
        values = [(str(r.value), int(r.cnt)) for r in rows if r.value is not None]

        # v1.24.6 — null_count: documents где cf_key отсутствует ИЛИ имеет null/пустую
        # строку. Используется для рендера опции «Не задано (N)» в TextFilterPopover'е.
        null_stmt = (
            select(func.count())
            .select_from(m.Document)
            .where(m.Document.deleted_at.is_(None))
            .where(
                or_(
                    ~m.Document.custom_field_values.has_key(cf_key),  # type: ignore[attr-defined]
                    m.Document.custom_field_values.op("->>")(cf_key).is_(None),
                    m.Document.custom_field_values.op("->>")(cf_key) == "",
                )
            )
        )
        null_result = await self._s.execute(null_stmt)
        null_count: int = null_result.scalar_one() or 0

        return values, total_distinct, null_count


# ---------------------------------------------------------------------------
# AttachmentRepository
# ---------------------------------------------------------------------------


class SqlAttachmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_id(self, attachment_id: uuid.UUID) -> Attachment | None:
        row = await self._s.get(m.Attachment, attachment_id)
        return _attachment_from_row(row) if row else None

    async def list_for_document(self, document_id: uuid.UUID) -> list[Attachment]:
        result = await self._s.execute(
            select(m.Attachment)
            .where(m.Attachment.document_id == document_id)
            .order_by(m.Attachment.created_at)
        )
        return [_attachment_from_row(row) for row in result.scalars().all()]

    async def add(self, attachment: Attachment) -> None:
        row = m.Attachment(
            id=attachment.id,
            document_id=attachment.document_id,
            original_filename=attachment.original_filename,
            mime_type=attachment.mime_type,
            size_bytes=attachment.size_bytes,
            sha256=attachment.sha256,
            storage_path=attachment.storage_path,
            created_by=attachment.created_by,
            created_at=attachment.created_at,
        )
        self._s.add(row)
        await self._s.flush()

    async def delete(self, attachment_id: uuid.UUID) -> None:
        row = await self._s.get(m.Attachment, attachment_id)
        if row:
            await self._s.delete(row)
            await self._s.flush()


# ---------------------------------------------------------------------------
# ExportJobRepository
# ---------------------------------------------------------------------------


class SqlExportJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_id(self, job_id: uuid.UUID) -> ExportJob | None:
        row = await self._s.get(m.ExportJob, job_id)
        return _export_job_from_row(row) if row else None

    async def add(self, job: ExportJob) -> None:

        row = m.ExportJob(
            id=job.id,
            requested_by=job.requested_by,
            status=job.status,
            file_path=job.file_path,
            error=job.error,
            expires_at=job.expires_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        self._s.add(row)
        await self._s.flush()

    async def update(self, job: ExportJob) -> None:
        await self._s.execute(
            update(m.ExportJob)
            .where(m.ExportJob.id == job.id)
            .values(
                status=job.status,
                file_path=job.file_path,
                error=job.error,
                expires_at=job.expires_at,
                updated_at=job.updated_at,
            )
        )

    async def list_expired_not_purged(self) -> list[ExportJob]:
        from datetime import UTC

        now = datetime.now(tz=UTC)
        result = await self._s.execute(
            select(m.ExportJob)
            .where(
                and_(
                    m.ExportJob.expires_at < now,
                    m.ExportJob.file_path.isnot(None),
                    m.ExportJob.status == "done",
                )
            )
            .order_by(m.ExportJob.expires_at)
        )
        return [_export_job_from_row(row) for row in result.scalars().all()]


# ---------------------------------------------------------------------------
# EventOutbox (transactional outbox writer)
# ---------------------------------------------------------------------------


class SqlEventOutbox:
    """Writes event envelopes to registry.outbox in the current session/transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def publish(self, envelope: Any, *, topic: str) -> None:
        row = m.Outbox(
            id=envelope.id,
            occurred_at=envelope.occurred_at,
            dispatched_at=None,
            topic=topic,
            payload=envelope.model_dump(mode="json"),
        )
        self._s.add(row)
        await self._s.flush()
