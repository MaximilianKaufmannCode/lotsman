# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Domain entities for registry-service.

Pure Python dataclasses — no SQLAlchemy, no FastAPI imports.
Only stdlib + domain value_objects allowed (Iron Rule 1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from registry_service.domain.custom_fields import CustomField

# ---------------------------------------------------------------------------
# Asset  (партнёрская компания)
# ---------------------------------------------------------------------------


_ASSET_VALID_STATUSES = frozenset({"active", "liquidating", "archived"})


@dataclass
class Asset:
    """Partner company master record.

    Dual-signal model (see docs/db/saved-filters-and-indexes.md §5):
      - status: functional/business state ('active' | 'liquidating' | 'archived')
      - deleted_at: soft-delete / recovery sentinel (NULL = present, non-NULL = trashed)

    When archiving, BOTH signals must be updated together (status='archived' + deleted_at=now).
    When restoring, BOTH must be reset (status='active' + deleted_at=None).
    'liquidating' is the only state where deleted_at IS NULL and status != 'active'.
    """

    id: uuid.UUID
    name: str
    inn: str | None
    notes: str | None
    status: str  # 'active' | 'liquidating' | 'archived'
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @classmethod
    def create(
        cls,
        *,
        name: str,
        inn: str | None = None,
        notes: str | None = None,
        status: str = "active",
        now: datetime | None = None,
    ) -> Asset:
        ts = now or datetime.now(tz=UTC)
        return cls(
            id=uuid.uuid4(),
            name=name,
            inn=inn,
            notes=notes,
            status=status,
            created_at=ts,
            updated_at=ts,
            deleted_at=None,
        )

    @property
    def is_active(self) -> bool:
        return self.deleted_at is None


# ---------------------------------------------------------------------------
# DocumentType  — notification cadence catalog
# ---------------------------------------------------------------------------


@dataclass
class DocumentType:
    """Document type catalog entry: contract, license, audit_report, etc."""

    code: str
    display_name: str
    pre_notice_days: list[int]
    notify_in_day: bool
    overdue_every_days: int
    created_at: datetime
    updated_at: datetime
    custom_field_schema: list[CustomField] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        code: str,
        display_name: str,
        pre_notice_days: list[int],
        notify_in_day: bool = True,
        overdue_every_days: int = 7,
        custom_field_schema: list[CustomField] | None = None,
        now: datetime | None = None,
    ) -> DocumentType:
        ts = now or datetime.now(tz=UTC)
        return cls(
            code=code,
            display_name=display_name,
            pre_notice_days=pre_notice_days,
            notify_in_day=notify_in_day,
            overdue_every_days=overdue_every_days,
            custom_field_schema=custom_field_schema or [],
            created_at=ts,
            updated_at=ts,
        )


# ---------------------------------------------------------------------------
# Document  — core entity
# ---------------------------------------------------------------------------


@dataclass
class Document:
    """A partner-company document (contract, license, report, etc.)."""

    id: uuid.UUID
    asset_id: uuid.UUID
    type_code: str
    number: str | None
    issue_date: date | None
    expiry_date: date | None
    responsible_user_id: uuid.UUID | None
    status: str  # "active" | "archived"
    notes: str | None
    created_by: uuid.UUID
    updated_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    custom_field_values: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        asset_id: uuid.UUID,
        type_code: str,
        number: str | None = None,
        issue_date: date | None = None,
        expiry_date: date | None = None,
        responsible_user_id: uuid.UUID | None = None,
        notes: str | None = None,
        created_by: uuid.UUID,
        custom_field_values: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> Document:
        ts = now or datetime.now(tz=UTC)
        return cls(
            id=uuid.uuid4(),
            asset_id=asset_id,
            type_code=type_code,
            number=number,
            issue_date=issue_date,
            expiry_date=expiry_date,
            responsible_user_id=responsible_user_id,
            status="active",
            notes=notes,
            created_by=created_by,
            updated_by=created_by,
            created_at=ts,
            updated_at=ts,
            deleted_at=None,
            custom_field_values=custom_field_values or {},
        )

    @property
    def is_active(self) -> bool:
        return self.deleted_at is None


# ---------------------------------------------------------------------------
# Attachment  — file metadata
# ---------------------------------------------------------------------------


@dataclass
class Attachment:
    """File attachment metadata. Bytes are stored on the attachments volume."""

    id: uuid.UUID
    document_id: uuid.UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_path: str
    created_by: uuid.UUID
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        document_id: uuid.UUID,
        original_filename: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        storage_path: str,
        created_by: uuid.UUID,
        now: datetime | None = None,
    ) -> Attachment:
        ts = now or datetime.now(tz=UTC)
        return cls(
            id=uuid.uuid4(),
            document_id=document_id,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            storage_path=storage_path,
            created_by=created_by,
            created_at=ts,
        )


# ---------------------------------------------------------------------------
# ExportJob
# ---------------------------------------------------------------------------


@dataclass
class ExportJob:
    """Tracks an xlsx export task lifecycle: pending → running → done | failed."""

    id: uuid.UUID
    requested_by: uuid.UUID
    status: str  # "pending" | "running" | "done" | "failed"
    file_path: str | None
    error: str | None
    expires_at: datetime | None
    filters: dict  # type: ignore[type-arg]  # snapshot of filter+sort+columns at request time
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        requested_by: uuid.UUID,
        filters: dict,  # type: ignore[type-arg]
        now: datetime | None = None,
    ) -> ExportJob:
        ts = now or datetime.now(tz=UTC)
        return cls(
            id=uuid.uuid4(),
            requested_by=requested_by,
            status="pending",
            file_path=None,
            error=None,
            expires_at=None,
            filters=filters,
            created_at=ts,
            updated_at=ts,
        )

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(tz=UTC) > self.expires_at
