# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for the markdown-subset → HTML email renderer.

Focus: the deep-link CTA must render as exactly ONE button without a duplicated
«Открыть документ» label paragraph (regression for the doubled-CTA bug seen in
reminder emails on PreProd).
"""

from __future__ import annotations

import pytest

from notification_service.infrastructure.email_html import render_markdown_subset

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
