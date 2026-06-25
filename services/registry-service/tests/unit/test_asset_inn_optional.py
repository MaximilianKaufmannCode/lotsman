# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ИНН is optional: blank/whitespace must coerce to None, not be rejected.

Regression for the bug where the (already-optional) INN field was effectively
required: the SPA sent "" for a blank field and the request schema rejected it
via min_length=10, surfacing as a 422 and blocking creation without an INN.
"""

from __future__ import annotations

import pytest

from registry_service.api.schemas import AssetCreateRequest, AssetUpdateRequest


@pytest.mark.parametrize("blank", ["", "   ", "\t", None])
def test_create_blank_inn_becomes_none(blank: str | None) -> None:
    req = AssetCreateRequest(name="ООО Ромашка", inn=blank)
    assert req.inn is None


def test_create_inn_omitted_is_none() -> None:
    assert AssetCreateRequest(name="ООО Ромашка").inn is None


def test_create_valid_inn_is_kept_and_trimmed() -> None:
    assert AssetCreateRequest(name="ООО Ромашка", inn="7707083893").inn == "7707083893"
    assert AssetCreateRequest(name="ООО Ромашка", inn=" 7707083893 ").inn == "7707083893"
    # 12-digit (ИП) also accepted by the schema
    assert AssetCreateRequest(name="ИП Иванов", inn="500100732259").inn == "500100732259"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_update_blank_inn_becomes_none(blank: str | None) -> None:
    assert AssetUpdateRequest(inn=blank).inn is None


def test_provided_inn_still_validated() -> None:
    from pydantic import ValidationError

    # too short (5 digits) — min_length=10 still enforced for a real value
    with pytest.raises(ValidationError):
        AssetCreateRequest(name="X", inn="12345")
    # non-digit content of valid length — digits-only validator still fires
    with pytest.raises(ValidationError):
        AssetCreateRequest(name="X", inn="abcdefghij")
