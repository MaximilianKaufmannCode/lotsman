# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for the minimal {{placeholder}} renderer."""

from __future__ import annotations

import pytest

from notification_service.infrastructure.templating import (
    TemplateRenderError,
    extract_placeholders,
    render_template,
)


class TestRenderTemplate:
    def test_simple_substitution(self) -> None:
        result = render_template("Hello, {{name}}!", {"name": "Иван"})
        assert result == "Hello, Иван!"

    def test_multiple_placeholders(self) -> None:
        result = render_template(
            "Документ {{number}} истекает через {{days}} дн.",
            {"number": "DOC-001", "days": 7},
        )
        assert result == "Документ DOC-001 истекает через 7 дн."

    def test_repeated_placeholder(self) -> None:
        result = render_template("{{name}}, {{name}}!", {"name": "Х"})
        assert result == "Х, Х!"

    def test_whitespace_inside_braces(self) -> None:
        result = render_template("{{ name }} and {{  age  }}", {"name": "А", "age": 30})
        assert result == "А and 30"

    def test_int_value_is_str(self) -> None:
        result = render_template("days={{n}}", {"n": 0})
        assert result == "days=0"

    def test_none_value_is_str_none(self) -> None:
        result = render_template("v={{x}}", {"x": None})
        assert result == "v=None"

    def test_missing_var_raises(self) -> None:
        with pytest.raises(TemplateRenderError) as ei:
            render_template("Hi, {{name}}!", {})
        assert "name" in str(ei.value)
        assert "Available" in str(ei.value)

    def test_template_without_placeholders_returns_unchanged(self) -> None:
        assert render_template("plain text", {}) == "plain text"
        assert render_template("", {}) == ""

    def test_markdown_bold_preserved(self) -> None:
        # The template body uses **bold** markdown. The renderer does NOT
        # interpret markdown — it only substitutes placeholders. Downstream
        # rendering of **bold** is the email-template-engine's job.
        result = render_template(
            "Документ **{{number}}** истекает",
            {"number": "DOC-1"},
        )
        assert result == "Документ **DOC-1** истекает"

    def test_unknown_chars_not_misinterpreted_as_placeholder(self) -> None:
        # Single braces, malformed, identifier starting with digit — all unchanged
        assert render_template("{x} {{ 1abc }} {{}}", {}) == "{x} {{ 1abc }} {{}}"

    def test_real_pre_notice_template(self) -> None:
        # Re-creating the actual production template body
        body = (
            "Уважаемый(ая) {{full_name}},\n\n"
            "Документ **{{document_number}}** ({{document_type}}) "
            "компании **{{asset_name}}** истекает **{{expiry_date}}** "
            "(через {{days_left}} дн.).\n\n"
            "Ответственный: {{responsible_name}}\n\n"
            "— Лоцман"
        )
        out = render_template(
            body,
            {
                "full_name": "Иванов И.И.",
                "document_number": "DOC-42",
                "document_type": "Лицензия",
                "asset_name": "ООО Ромашка",
                "expiry_date": "2026-06-15",
                "days_left": 7,
                "responsible_name": "Петров П.П.",
            },
        )
        assert "Иванов И.И." in out
        assert "DOC-42" in out
        assert "Лицензия" in out
        assert "Ромашка" in out
        assert "2026-06-15" in out
        assert "через 7 дн." in out
        assert "Петров П.П." in out
        assert "{{" not in out  # all placeholders substituted


class TestExtractPlaceholders:
    def test_simple(self) -> None:
        assert extract_placeholders("Hi, {{name}}!") == {"name"}

    def test_multiple_unique(self) -> None:
        assert extract_placeholders("{{a}} {{b}} {{a}}") == {"a", "b"}

    def test_none(self) -> None:
        assert extract_placeholders("plain") == set()

    def test_whitespace(self) -> None:
        assert extract_placeholders("{{  x  }} {{y}}") == {"x", "y"}
