# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for validate_values_against_schema."""

from __future__ import annotations

from datetime import date

import pytest

from registry_service.domain.custom_fields import (
    CustomField,
    CustomFieldValidationError,
    FieldType,
    validate_value,
    validate_values_against_schema,
)

_TEXT = CustomField(key="description", display_name="Description", type=FieldType.TEXT)
_NUMBER = CustomField(key="amount", display_name="Amount", type=FieldType.NUMBER)
_DATE = CustomField(key="signed_on", display_name="Signed On", type=FieldType.DATE)
_ENUM = CustomField(
    key="category",
    display_name="Category",
    type=FieldType.ENUM,
    options=["A", "B", "C"],
)
_REQUIRED = CustomField(
    key="required_field",
    display_name="Required",
    type=FieldType.TEXT,
    required=True,
)


class TestValidateValue:
    # --- TEXT ---
    def test_text_passthrough(self) -> None:
        assert validate_value(_TEXT, "hello") == "hello"

    def test_text_coerce_non_string(self) -> None:
        assert validate_value(_TEXT, 42) == "42"

    def test_text_too_long(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="10,000"):
            validate_value(_TEXT, "x" * 10_001)

    # --- NUMBER ---
    def test_number_int(self) -> None:
        assert validate_value(_NUMBER, 10) == 10

    def test_number_float(self) -> None:
        assert validate_value(_NUMBER, 3.14) == 3.14

    def test_number_string_int(self) -> None:
        assert validate_value(_NUMBER, "42") == 42

    def test_number_string_float(self) -> None:
        result = validate_value(_NUMBER, "3.5")
        assert result == 3.5

    def test_number_nan_rejected(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="NaN"):
            validate_value(_NUMBER, float("nan"))

    def test_number_inf_rejected(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="Inf"):
            validate_value(_NUMBER, float("inf"))

    def test_number_bool_rejected(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="boolean"):
            validate_value(_NUMBER, True)

    def test_number_bad_string(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="cannot convert"):
            validate_value(_NUMBER, "not-a-number")

    # --- DATE ---
    def test_date_iso_string(self) -> None:
        result = validate_value(_DATE, "2025-12-31")
        assert result == date(2025, 12, 31)

    def test_date_date_object(self) -> None:
        d = date(2024, 1, 15)
        assert validate_value(_DATE, d) is d

    def test_date_invalid_string(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="parse"):
            validate_value(_DATE, "31-12-2025")

    # --- ENUM ---
    def test_enum_valid(self) -> None:
        assert validate_value(_ENUM, "A") == "A"

    def test_enum_with_strip(self) -> None:
        assert validate_value(_ENUM, " B ") == "B"

    def test_enum_not_in_options(self) -> None:
        with pytest.raises(CustomFieldValidationError, match="not in options"):
            validate_value(_ENUM, "D")


class TestValidateValuesAgainstSchema:
    def test_all_valid_values_returned(self) -> None:
        schema = [_TEXT, _NUMBER, _DATE, _ENUM]
        values = {
            "description": "hello",
            "amount": 100,
            "signed_on": "2024-06-01",
            "category": "B",
        }
        result = validate_values_against_schema(schema, values)
        assert result["description"] == "hello"
        assert result["amount"] == 100
        assert result["signed_on"] == date(2024, 6, 1)
        assert result["category"] == "B"

    def test_unknown_keys_dropped_silently(self) -> None:
        schema = [_TEXT]
        values = {"description": "hi", "nonexistent_key": "ignored"}
        result = validate_values_against_schema(schema, values)
        assert "nonexistent_key" not in result
        assert result["description"] == "hi"

    def test_required_field_missing_raises(self) -> None:
        schema = [_REQUIRED]
        with pytest.raises(CustomFieldValidationError, match="required_field"):
            validate_values_against_schema(schema, {})

    def test_required_field_none_raises(self) -> None:
        schema = [_REQUIRED]
        with pytest.raises(CustomFieldValidationError, match="required_field"):
            validate_values_against_schema(schema, {"required_field": None})

    def test_optional_field_absent_ok(self) -> None:
        schema = [_TEXT, _NUMBER]
        result = validate_values_against_schema(schema, {"amount": 5})
        assert "description" not in result
        assert result["amount"] == 5

    def test_type_mismatch_raises(self) -> None:
        schema = [_NUMBER]
        with pytest.raises(CustomFieldValidationError):
            validate_values_against_schema(schema, {"amount": "not-a-number"})

    def test_empty_schema_empty_result(self) -> None:
        result = validate_values_against_schema([], {"random": "value"})
        assert result == {}

    def test_enum_membership_enforced(self) -> None:
        schema = [_ENUM]
        with pytest.raises(CustomFieldValidationError, match="not in options"):
            validate_values_against_schema(schema, {"category": "Z"})
