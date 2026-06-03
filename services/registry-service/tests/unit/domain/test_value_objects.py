# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Tests for domain value objects."""

from __future__ import annotations

import pytest

from registry_service.domain.value_objects import (
    INN,
    AssetName,
    AttachmentMime,
    DocumentNumber,
    DocumentStatus,
    ExportFormat,
)


class TestINN:
    def test_valid_10_digit_legal_entity(self) -> None:
        # ФНС reference INN: 7701234567 — well-known public test value
        # Use a valid INN with correct checksum.
        # INN 7707083893 (Sberbank) — publicly known valid INN
        inn = INN("7707083893")
        assert str(inn) == "7707083893"

    def test_valid_12_digit_individual(self) -> None:
        # Use ФНС reference: 500100732259
        inn = INN("500100732259")
        assert str(inn) == "500100732259"

    def test_invalid_checksum_10_digit(self) -> None:
        with pytest.raises(ValueError, match="checksum"):
            INN("7707083890")  # last digit wrong

    def test_invalid_length_too_short(self) -> None:
        with pytest.raises(ValueError, match="10 or 12 digits"):
            INN("123456789")

    def test_invalid_length_11_digits(self) -> None:
        with pytest.raises(ValueError, match="10 or 12 digits"):
            INN("12345678901")

    def test_non_digit_characters(self) -> None:
        with pytest.raises(ValueError):
            INN("770708ABC3")

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError):
            INN("")

    def test_strips_whitespace(self) -> None:
        inn = INN(" 7707083893 ")
        assert str(inn) == "7707083893"


class TestAssetName:
    def test_valid_name(self) -> None:
        name = AssetName("ООО Ромашка")
        assert str(name) == "ООО Ромашка"

    def test_strips_whitespace(self) -> None:
        name = AssetName("  ООО Ромашка  ")
        assert str(name) == "ООО Ромашка"

    def test_empty_after_strip_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            AssetName("   ")

    def test_too_long_raises(self) -> None:
        with pytest.raises(ValueError, match="500"):
            AssetName("A" * 501)

    def test_exactly_500_chars_is_valid(self) -> None:
        name = AssetName("A" * 500)
        assert len(name) == 500


class TestDocumentStatus:
    def test_display_labels(self) -> None:
        assert DocumentStatus.ok.display_label_ru == "ОК"
        assert DocumentStatus.soon.display_label_ru == "Скоро"
        assert DocumentStatus.overdue.display_label_ru == "Просрочено"
        assert DocumentStatus.archived.display_label_ru == "В архиве"

    def test_enum_values(self) -> None:
        assert DocumentStatus.ok.value == "ok"
        assert DocumentStatus.archived.value == "archived"


class TestDocumentNumber:
    def test_valid_number(self) -> None:
        num = DocumentNumber("АО-2025-01")
        assert str(num) == "АО-2025-01"

    def test_empty_is_valid(self) -> None:
        num = DocumentNumber("")
        assert str(num) == ""

    def test_too_long_raises(self) -> None:
        with pytest.raises(ValueError, match="500"):
            DocumentNumber("X" * 501)


class TestAttachmentMime:
    def test_all_allowed_types_in_enum(self) -> None:
        values = {m.value for m in AttachmentMime}
        assert "application/pdf" in values
        assert "image/jpeg" in values
        assert "image/png" in values
        assert "image/tiff" in values

    def test_docx_value(self) -> None:
        assert AttachmentMime.docx.value == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )


class TestExportFormat:
    def test_xlsx_value(self) -> None:
        assert ExportFormat.xlsx.value == "xlsx"
