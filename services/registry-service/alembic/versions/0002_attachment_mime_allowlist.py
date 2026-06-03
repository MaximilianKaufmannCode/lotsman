# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add CHECK constraint for attachment MIME type allowlist (Q7).

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-07

Allowed MIME types (Q7 acceptance decision):
  - application/pdf
  - image/jpeg
  - image/png
  - image/tiff
  - application/vnd.openxmlformats-officedocument.wordprocessingml.document (docx)
  - application/vnd.openxmlformats-officedocument.spreadsheetml.sheet (xlsx)

The application layer enforces this via attachment_policy.py; the DB constraint
is a defense-in-depth measure to prevent direct-SQL inserts of forbidden types.
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str = "0001"
branch_labels = None
depends_on = None

_ALLOWED_MIME = (
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

_MIME_LIST = ", ".join(f"'{m}'" for m in _ALLOWED_MIME)
_CONSTRAINT_EXPR = f"mime_type IN ({_MIME_LIST})"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE registry.attachments
            ADD CONSTRAINT attachments_mime_type_check
            CHECK ({_CONSTRAINT_EXPR})
        """
    )
    op.execute(
        "COMMENT ON CONSTRAINT attachments_mime_type_check ON registry.attachments IS "
        "'Q7: MIME allowlist — PDF, JPEG, PNG, TIFF, DOCX, XLSX only.'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE registry.attachments DROP CONSTRAINT IF EXISTS attachments_mime_type_check"
    )
