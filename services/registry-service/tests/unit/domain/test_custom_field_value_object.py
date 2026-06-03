# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for CustomField value object validation."""

from __future__ import annotations

import pytest

from registry_service.domain.custom_fields import CustomField, CustomFieldValidationError, FieldType


class TestCustomFieldKeyValidation:
    def test_valid_key(self) -> None:
        cf = CustomField(key="my_field", display_name="My Field", type=FieldType.TEXT)
        assert cf.key == "my_field"

    def test_key_too_short_digit_start(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="key"):
            CustomField(key="1bad", display_name="Bad Key", type=FieldType.TEXT)

    def test_key_uppercase_rejected(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="key"):
            CustomField(key="MyField", display_name="Bad", type=FieldType.TEXT)

    def test_key_with_hyphens_rejected(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="key"):
            CustomField(key="my-field", display_name="Bad", type=FieldType.TEXT)

    def test_key_too_long(self) -> None:
        # 65 chars: 1 prefix + 64 suffix = 65 total → exceeds {0,63} suffix limit
        long_key = "a" + "b" * 64  # 65 chars total
        with pytest.raises(CustomFieldValidationError, match="key"):
            CustomField(key=long_key, display_name="Long", type=FieldType.TEXT)

    def test_key_max_valid_length(self) -> None:
        # 64 chars: 1 prefix + 63 suffix = exactly on the boundary
        key = "a" + "b" * 63
        cf = CustomField(key=key, display_name="Max Length", type=FieldType.TEXT)
        assert len(cf.key) == 64

    def test_empty_key_rejected(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="key"):
            CustomField(key="", display_name="Empty", type=FieldType.TEXT)


class TestCustomFieldDisplayName:
    def test_display_name_empty(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="display_name"):
            CustomField(key="field", display_name="", type=FieldType.TEXT)

    def test_display_name_too_long(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="display_name"):
            CustomField(key="field", display_name="x" * 101, type=FieldType.TEXT)

    def test_display_name_at_max(self) -> None:
        cf = CustomField(key="field", display_name="x" * 100, type=FieldType.TEXT)
        assert len(cf.display_name) == 100


class TestCustomFieldEnumOptions:
    def test_enum_without_options_raises(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="enum"):
            CustomField(key="status", display_name="Status", type=FieldType.ENUM, options=None)

    def test_enum_empty_options_raises(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="enum"):
            CustomField(key="status", display_name="Status", type=FieldType.ENUM, options=[])

    def test_enum_with_options_valid(self) -> None:
        cf = CustomField(
            key="status",
            display_name="Status",
            type=FieldType.ENUM,
            options=["active", "inactive"],
        )
        assert cf.options == ["active", "inactive"]

    def test_non_enum_with_options_raises(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="must not have options"):
            CustomField(
                key="notes",
                display_name="Notes",
                type=FieldType.TEXT,
                options=["a", "b"],
            )

    def test_number_with_options_raises(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="must not have options"):
            CustomField(key="amount", display_name="Amount", type=FieldType.NUMBER, options=["1"])

    def test_date_with_options_raises(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="must not have options"):
            CustomField(key="dob", display_name="DoB", type=FieldType.DATE, options=["2024-01-01"])


class TestCustomFieldSerialization:
    def test_round_trip_text(self) -> None:
        cf = CustomField(key="notes", display_name="Notes", type=FieldType.TEXT, required=True)
        d = cf.to_dict()
        restored = CustomField.from_dict(d)
        assert restored == cf

    def test_round_trip_enum(self) -> None:
        cf = CustomField(
            key="status",
            display_name="Status",
            type=FieldType.ENUM,
            required=False,
            options=["draft", "final"],
        )
        restored = CustomField.from_dict(cf.to_dict())
        assert restored == cf

    def test_to_dict_excludes_none_options(self) -> None:
        cf = CustomField(key="value", display_name="Value", type=FieldType.NUMBER)
        d = cf.to_dict()
        assert "options" not in d
