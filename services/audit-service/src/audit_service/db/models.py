# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""SQLAlchemy 2.x ORM models for the audit schema.

audit.events is a partitioned table (RANGE on occurred_at, monthly).
SQLAlchemy does not autogenerate PARTITION BY DDL — the migration handles that
with op.execute() raw SQL. This model exists so autogenerate can detect schema
drift on the non-partitioned columns.

audit-service has NO outbox table: it is a terminal sink, never a publisher.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Text
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSONB, UUID
TIMESTAMPTZ = DateTime(timezone=True)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# audit.events  — append-only, partitioned by RANGE (occurred_at)
#
# NOTE: The composite PK (id, occurred_at) is required by PostgreSQL for
# declarative partitioning — the partition key must be part of the PK.
#
# The table is created via op.execute() in the migration (not op.create_table)
# because autogenerate cannot emit PARTITION BY RANGE. This model is kept in
# sync with the migration DDL manually.
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    __tablename__ = "events"
    __table_args__ = (
        {
            "schema": "audit",
            "comment": (
                "Append-only audit event log. Partitioned by RANGE (occurred_at), "
                "one partition per calendar month. "
                "UPDATE and DELETE are revoked from audit_app."
            ),
            # postgresql_partition_by tells SQLA the table is partitioned.
            # The actual PARTITION BY RANGE clause is emitted by the migration.
            "postgresql_partition_by": "RANGE (occurred_at)",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        comment="Event UUID. Part of composite PK with occurred_at for partitioning.",
    )
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        primary_key=True,
        comment="When the event occurred. Partition key — must be set by the caller, not defaulted.",
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="UUID of the acting user or system actor (see docs/db/system-actors.md).",
    )
    entity_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Aggregate type string, e.g. 'document', 'asset', 'user'.",
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="UUID of the affected entity.",
    )
    event_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Namespaced event type, e.g. 'registry.document.created.v1'.",
    )
    payload: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        JSONB,
        nullable=False,
        comment="Full canonical event envelope payload (before/after, diff, etc.).",
    )
    request_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Propagated X-Request-Id from the inbound HTTP request for end-to-end tracing.",
    )
