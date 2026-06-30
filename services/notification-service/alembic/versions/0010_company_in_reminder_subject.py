# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Add the company name to deadline reminder email subjects.

Revision ID: 0010_company_in_reminder_subject
Revises: 0009_richer_email_templates
Create Date: 2026-06-30

A recipient looking at their inbox could not tell which company a document
belonged to — the subject only carried the document number. The email body
already shows the company in its details block (once the registry detail
endpoint resolves `asset_name`), but the subject is what you see *before*
opening the message. Append « · {{asset_name}}» to the three deadline subjects.

DATA-only, additive and fully reversible: updates the subject of 3 email rows
(pre_notice / in_day / overdue, locale ru); downgrade restores the 0009 text.
No schema change; body_md and telegram/dion rows are untouched. `asset_name`
is already supplied to the renderer, so no template variable is newly required.
"""

from __future__ import annotations

from alembic import op

revision: str = "0010_company_in_reminder_subject"
down_revision = "0009_richer_email_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: срок актуализации через {{days_left_phrase}} — {{document_number}} · {{asset_name}}'
        WHERE channel = 'email' AND template_code = 'pre_notice' AND locale = 'ru'
    """)
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: документ истекает сегодня — {{document_number}} · {{asset_name}}'
        WHERE channel = 'email' AND template_code = 'in_day' AND locale = 'ru'
    """)
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: документ просрочен на {{days_overdue_phrase}} — {{document_number}} · {{asset_name}}'
        WHERE channel = 'email' AND template_code = 'overdue' AND locale = 'ru'
    """)


def downgrade() -> None:
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: срок актуализации через {{days_left_phrase}} — {{document_number}}'
        WHERE channel = 'email' AND template_code = 'pre_notice' AND locale = 'ru'
    """)
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: документ истекает сегодня — {{document_number}}'
        WHERE channel = 'email' AND template_code = 'in_day' AND locale = 'ru'
    """)
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: документ просрочен на {{days_overdue_phrase}} — {{document_number}}'
        WHERE channel = 'email' AND template_code = 'overdue' AND locale = 'ru'
    """)
