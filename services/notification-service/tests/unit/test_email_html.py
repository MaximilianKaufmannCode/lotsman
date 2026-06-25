# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for the markdown-subset → HTML email renderer.

Focus: the deep-link CTA must render as exactly ONE button without a duplicated
«Открыть документ» label paragraph (regression for the doubled-CTA bug seen in
reminder emails on PreProd).
"""

from __future__ import annotations

import pytest

from notification_service.infrastructure.email_html import (
    STATUS_ACCENT,
    render_markdown_subset,
    render_notification_email,
)

_URL = "https://lotsman.example.com/registry?document_id=b4b5fd87-94b7-4e48"


@pytest.mark.parametrize(
    ("paragraph", "expected_prefix"),
    [
        # pre_notice: the whole prefix IS the CTA label → no prefix paragraph.
        (f"Открыть документ в системе:\n{_URL}", None),
        # in_day / overdue: meaningful sentence + redundant CTA label → keep the
        # sentence, drop the «Открыть документ:» label.
        (f"Требуется актуализация. Открыть документ:\n{_URL}", "Требуется актуализация."),
        (
            f"Требуется немедленная актуализация. Открыть документ:\n{_URL}",
            "Требуется немедленная актуализация.",
        ),
    ],
)
def test_deep_link_cta_renders_single_button(
    paragraph: str, expected_prefix: str | None
) -> None:
    html = render_markdown_subset(paragraph)

    # The button caption «Открыть документ» must appear exactly once.
    assert html.count("Открыть документ") == 1
    # The deep link is wired into an anchor.
    assert f'href="{_URL}"' in html

    if expected_prefix is None:
        # No standalone prefix paragraph before the button.
        assert "<p" in html
        # Only the button paragraph remains (no leftover label text).
        assert ">Открыть документ</a>" in html
    else:
        assert expected_prefix in html


def test_plain_paragraph_without_link_is_untouched() -> None:
    html = render_markdown_subset("Уважаемый(ая) **Иван**,")
    assert "<strong>Иван</strong>" in html
    assert "<a " not in html


# ── render_notification_email (structured design: status accent + details + CTA) ──


def test_notification_email_structure():
    html_out = render_notification_email(
        subject="Лоцман: срок через 3 дня",
        headline="Срок актуализации через 3 дня",
        intro_html="<p>Здравствуйте, Иван.</p>",
        details=[
            ("Компания", "ООО «Ромашка»"),
            ("№ документа", "Д-15/2026"),
            ("Срок действия", "15 июля 2026, ср"),
            ("Пусто", "—"),      # must be skipped
            ("Пусто2", ""),       # must be skipped
        ],
        cta_url="https://lotsman.example.com/registry?document_id=abc",
        settings_url="https://lotsman.example.com/profile",
        status="soon",
    )
    # Status accent colour present (amber for "soon").
    assert STATUS_ACCENT["soon"] in html_out
    # Headline + intro present.
    assert "Срок актуализации через 3 дня" in html_out
    assert "Здравствуйте, Иван." in html_out
    # Details rendered; empty/«—» rows skipped.
    assert "ООО «Ромашка»" in html_out
    assert "Д-15/2026" in html_out
    assert "15 июля 2026, ср" in html_out
    assert "Пусто" not in html_out
    # CTA button (url + label) + settings link.
    assert "https://lotsman.example.com/registry?document_id=abc" in html_out
    assert "Открыть документ" in html_out
    assert "https://lotsman.example.com/profile" in html_out
    assert "Настроить уведомления" in html_out
    # Dark-mode support + branding.
    assert "prefers-color-scheme: dark" in html_out
    assert "⚓ Лоцман" in html_out


def test_notification_email_status_colours_differ():
    overdue = render_notification_email(subject="s", headline="h", status="overdue")
    soon = render_notification_email(subject="s", headline="h", status="soon")
    assert STATUS_ACCENT["overdue"] in overdue
    assert STATUS_ACCENT["soon"] in soon
    assert STATUS_ACCENT["overdue"] != STATUS_ACCENT["soon"]
    # unknown status falls back to "info"
    info = render_notification_email(subject="s", headline="h", status="???")
    assert STATUS_ACCENT["info"] in info


def test_notification_email_escapes_values():
    out = render_notification_email(
        subject="s",
        headline="h",
        details=[("Компания", "<script>x</script>")],
        status="info",
    )
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out


def test_notification_email_no_cta_when_no_url():
    out = render_notification_email(subject="s", headline="h", status="info")
    assert "Открыть документ" not in out
