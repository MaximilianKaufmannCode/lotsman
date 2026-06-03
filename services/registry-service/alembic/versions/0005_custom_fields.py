# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Adds JSONB custom_field_schema (per document type) and custom_field_values
(per document), with GIN index for future @> search.

Non-destructive — DEFAULT '[]'/'{}' for existing rows.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-08

Design decisions:
- custom_field_schema lives on document_types (one schema definition shared by
  all documents of that type). Shape: JSON array of field-descriptor objects,
  e.g. [{"key": "counterparty_inn", "label": "ИНН контрагента", "type": "text",
  "required": false}]. Parsed and validated at the application layer (Phase 2).
- custom_field_values lives on documents. Shape: flat JSON object keyed by the
  field key defined in the type's schema, e.g. {"counterparty_inn": "7701234567"}.
- GIN index uses jsonb_path_ops opclass — smaller and faster than default
  jsonb_ops for our use case (containment queries @> only; no key-existence
  checks needed here).
- No GRANT needed: registry_app already has DML on these tables from migration
  0001.

DOWNGRADE WARNING: destructive. Any custom_field_values stored in documents
will be permanently lost. Caller must back up the registry schema before
running downgrade in production.
"""

from __future__ import annotations

from alembic import op

revision: str = "0005"
down_revision: str = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. document_types: add schema definition column (per-type, shared by all
    #    documents of this type). Serves the "define custom fields for this type"
    #    admin UI (Phase 3).
    op.execute("""
        ALTER TABLE registry.document_types
            ADD COLUMN custom_field_schema JSONB NOT NULL DEFAULT '[]'::jsonb
    """)
    op.execute(
        "COMMENT ON COLUMN registry.document_types.custom_field_schema IS "
        "'JSON array of field-descriptor objects defining per-type custom fields. "
        "Shape validated at application layer. Empty array = no custom fields.'"
    )

    # 2. documents: add per-document values column. Stores the actual values
    #    supplied by the user for the custom fields declared in the type's schema.
    op.execute("""
        ALTER TABLE registry.documents
            ADD COLUMN custom_field_values JSONB NOT NULL DEFAULT '{}'::jsonb
    """)
    op.execute(
        "COMMENT ON COLUMN registry.documents.custom_field_values IS "
        "'Flat JSON object keyed by field key from document_types.custom_field_schema. "
        "Validated against the type schema at write time (application layer).'"
    )

    # 3. GIN index for @> containment queries (search/filter by custom field
    #    value). jsonb_path_ops is smaller and faster than default jsonb_ops
    #    when only @> operator is needed (no key-existence ? checks).
    op.execute("""
        CREATE INDEX documents_custom_fields_gin_idx
            ON registry.documents USING GIN (custom_field_values jsonb_path_ops)
    """)


def downgrade() -> None:
    # WARNING: destructive — accumulated custom_field_values data will be lost.
    # Pre-condition: caller must back up the registry schema before running this
    # in production.
    op.execute("DROP INDEX IF EXISTS registry.documents_custom_fields_gin_idx")
    op.execute("ALTER TABLE registry.documents DROP COLUMN IF EXISTS custom_field_values")
    op.execute("ALTER TABLE registry.document_types DROP COLUMN IF EXISTS custom_field_schema")
