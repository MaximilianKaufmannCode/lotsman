# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""SQLAlchemy 2.x ORM models for the registry schema.

Tables owned by registry-service:
  - registry.assets          (партнёрские компании, soft-deleted)
  - registry.document_types  (static catalog, code PK)
  - registry.documents       (core entity, soft-deleted)
  - registry.attachments     (file metadata per document)
  - registry.export_jobs     (xlsx export task tracking)
  - registry.outbox          (transactional outbox)
  - registry.outbox_dlq      (dead-letter queue)

No cross-schema FKs. responsible_user_id and created_by / updated_by are
bare UUIDs with comments naming the logical referent (auth.users).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# TIMESTAMPTZ is not exposed by SQLAlchemy's postgresql dialect directly;
# use DateTime(timezone=True) which maps to TIMESTAMPTZ in PostgreSQL.
TIMESTAMPTZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# registry.assets  — partner companies
# ---------------------------------------------------------------------------


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'liquidating', 'archived')",
            name="assets_status_check",
        ),
        {
            "schema": "registry",
            "comment": "Partner companies (партнёрские компании). Soft-deleted.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Company display name. Partial unique index enforces uniqueness among non-deleted.",
    )
    inn: Mapped[str | None] = mapped_column(
        String(12),
        nullable=True,
        comment="ИНН (Russian tax ID). Optional; no uniqueness enforced at DB level.",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=sa_text("'active'"),
        comment=(
            "Functional status of the partner company: "
            "active = operating normally (default); "
            "liquidating = in wind-down / liquidation process; "
            "archived = no longer active. "
            "Distinct from deleted_at (soft-delete). "
            "Added by migration 0008_add_asset_status."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ, nullable=True, comment="Soft-delete. NULL = active."
    )


# ---------------------------------------------------------------------------
# registry.document_types  — static catalog
# ---------------------------------------------------------------------------


class DocumentType(Base):
    __tablename__ = "document_types"
    __table_args__ = (
        {
            "schema": "registry",
            "comment": "Document type catalog: contract, license, audit_report, etc.",
        },
    )

    code: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        comment="Stable lowercase identifier, e.g. 'contract', 'license'.",
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    pre_notice_days: Mapped[list[int]] = mapped_column(
        ARRAY(Integer),
        nullable=False,
        server_default=sa_text("'{30,7,1}'::int[]"),
        comment="Days before expiry to send pre-notice notifications.",
    )
    notify_in_day: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("true"),
        comment="Whether to send an in-day notification on the expiry date itself.",
    )
    overdue_every_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=sa_text("7"),
        comment="Repeat overdue notification every N days until document is archived/replaced.",
    )
    # Added by migration 0005 (flexible-document-fields Phase 1).
    custom_field_schema: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa_text("'[]'::jsonb"),
        comment="JSON array of field-descriptor objects defining per-type custom fields. "
        "Empty array = no custom fields. Validated at application layer.",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )


# ---------------------------------------------------------------------------
# registry.documents
# ---------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="documents_status_check"),
        {
            "schema": "registry",
            "comment": "Partner-company documents (contracts, licenses, reports). Soft-deleted.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    # Intra-schema FK — asset must exist in registry.
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="FK → registry.assets(id). ON DELETE RESTRICT.",
    )
    type_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="FK → registry.document_types(code).",
    )
    number: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Document number / identifier (free text). GIN trgm index for search.",
    )
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Expiry date. Indexed for the notification scheduler's hot query.",
    )
    # Logical reference to auth.users — not a DB FK (cross-schema).
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Logical ref to auth.users(id). No DB FK — cross-schema. "
        "Nullified by registry-orphan-watch consumer on auth.user.deactivated.v1.",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=sa_text("'active'"),
        comment="active | archived",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # created_by / updated_by: logical refs to auth.users.
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Logical ref to auth.users(id) — actor who created this record.",
    )
    updated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Logical ref to auth.users(id) — actor who last updated this record.",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ, nullable=True, comment="Soft-delete. NULL = active."
    )
    # Added by migration 0005 (flexible-document-fields Phase 1).
    custom_field_values: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa_text("'{}'::jsonb"),
        comment="Flat JSON object keyed by field key from document_types.custom_field_schema. "
        "Validated against the type schema at write time (application layer).",
    )


# ---------------------------------------------------------------------------
# registry.attachments  — file metadata per document
# ---------------------------------------------------------------------------


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        {
            "schema": "registry",
            "comment": "File attachment metadata. Actual bytes stored on local volume; path in storage_path.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="FK → registry.documents(id). ON DELETE CASCADE.",
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Hex-encoded SHA-256 of the file content for integrity verification.",
    )
    storage_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Relative path under the attachments volume root.",
    )
    # Logical ref to auth.users.
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Logical ref to auth.users(id).",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )


# ---------------------------------------------------------------------------
# registry.export_jobs  — xlsx export task tracking
# ---------------------------------------------------------------------------


class ExportJob(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')",
            name="export_jobs_status_check",
        ),
        {
            "schema": "registry",
            "comment": "Tracks xlsx export tasks requested by users.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Logical ref to auth.users(id).",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=sa_text("'pending'"),
        comment="pending | running | done | failed",
    )
    file_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Relative path under the exports volume. Populated when status=done.",
    )
    error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Error message when status=failed."
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
        comment="When the export file should be deleted from disk.",
    )
    filters: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=False,
        server_default="'{}'::jsonb",
        comment="Snapshot of filter+sort+visible_columns at job submission time (Q2).",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=sa_text("now()"),
        comment="Touched by trigger on every UPDATE.",
    )


# ---------------------------------------------------------------------------
# registry.outbox
# ---------------------------------------------------------------------------


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        {"schema": "registry", "comment": "Transactional outbox for registry domain events."},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
        comment="Set by outbox-dispatcher ARQ worker after XADD to Redis Streams.",
    )
    topic: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Redis Stream key, e.g. 'registry.documents', 'registry.assets'.",
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]


# ---------------------------------------------------------------------------
# registry.outbox_dlq
# ---------------------------------------------------------------------------


class OutboxDlq(Base):
    __tablename__ = "outbox_dlq"
    __table_args__ = (
        {
            "schema": "registry",
            "comment": "Dead-letter queue for registry.outbox rows that failed all dispatch retries.",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    failed_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
    last_error: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=sa_text("now()")
    )
