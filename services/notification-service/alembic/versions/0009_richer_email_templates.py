# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redesign deadline email templates — concise intro + human-readable copy.

Revision ID: 0009_richer_email_templates
Revises: 0008
Create Date: 2026-06-25

The HTML renderer now builds the status accent, the at-a-glance details block
(Компания / Тип / № / Срок / Осталось / Ответственный) and the «Открыть
документ» button in code (email_html.render_notification_email). So the stored
`body_md` only needs a short human intro — the previous verbose body would
duplicate the details block. Subjects gain the human day phrase
({{days_left_phrase}}) and the document number for inbox scannability.

DATA-only, additive and fully reversible: updates 3 email rows
(pre_notice / in_day / overdue, locale ru); downgrade restores the original
0001 copy verbatim. No schema change; telegram/dion rows are untouched.
"""

from __future__ import annotations

from alembic import op

revision: str = "0009_richer_email_templates"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: срок актуализации через {{days_left_phrase}} — {{document_number}}',
            body_md = E'Здравствуйте, {{full_name}}.\n\nПо документу ниже приближается срок актуализации — проверьте его и при необходимости обновите.'
        WHERE channel = 'email' AND template_code = 'pre_notice' AND locale = 'ru'
    """)
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: документ истекает сегодня — {{document_number}}',
            body_md = E'Здравствуйте, {{full_name}}.\n\nСрок действия документа истекает сегодня. Откройте документ, чтобы продлить или актуализировать его.'
        WHERE channel = 'email' AND template_code = 'in_day' AND locale = 'ru'
    """)
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: документ просрочен на {{days_overdue_phrase}} — {{document_number}}',
            body_md = E'Здравствуйте, {{full_name}}.\n\nСрок действия документа истёк — требуется актуализация.'
        WHERE channel = 'email' AND template_code = 'overdue' AND locale = 'ru'
    """)


def downgrade() -> None:
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: срок актуализации документа через {{days_left}} дн.',
            body_md = E'Уважаемый(ая) {{full_name}},\n\nДокумент **{{document_number}}** ({{document_type}})\nкомпании **{{asset_name}}** истекает **{{expiry_date}}** (через {{days_left}} дн.).\n\nОтветственный: {{responsible_name}}\n\n— Лоцман'
        WHERE channel = 'email' AND template_code = 'pre_notice' AND locale = 'ru'
    """)
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: документ истекает сегодня',
            body_md = E'Уважаемый(ая) {{full_name}},\n\nДокумент **{{document_number}}** истекает **сегодня**.\n\n— Лоцман'
        WHERE channel = 'email' AND template_code = 'in_day' AND locale = 'ru'
    """)
    op.execute(r"""
        UPDATE notification.message_templates
        SET subject = 'Лоцман: документ просрочен {{days_overdue}} дн.',
            body_md = E'Уважаемый(ая) {{full_name}},\n\nДокумент **{{document_number}}** просрочен на {{days_overdue}} дн.\n\n— Лоцман'
        WHERE channel = 'email' AND template_code = 'overdue' AND locale = 'ru'
    """)
