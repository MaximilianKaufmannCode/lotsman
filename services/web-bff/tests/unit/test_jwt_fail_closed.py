# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests: web-bff JWT verification is fail-closed (security remediation 2026-06).

Without an RS256 public key, web-bff must REFUSE to start unless the explicit dev
opt-in (JWT_ALLOW_UNVERIFIED=true) is set — preventing the unverified-token fallback
from ever activating by misconfiguration in staging/prod.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from web_bff.config import Settings


def _set_required_internal_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_JWT_KEY_AUTH", "a" * 32)
    monkeypatch.setenv("INTERNAL_JWT_KEY_REGISTRY", "b" * 32)
    monkeypatch.setenv("INTERNAL_JWT_KEY_NOTIFICATION", "c" * 32)
    monkeypatch.setenv("INTERNAL_JWT_KEY_AUDIT", "d" * 32)


def test_missing_key_without_optin_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_internal_keys(monkeypatch)
    monkeypatch.delenv("JWT_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("JWT_ALLOW_UNVERIFIED", raising=False)  # remove conftest default
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_dev_optin_allows_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_internal_keys(monkeypatch)
    monkeypatch.delenv("JWT_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.setenv("JWT_ALLOW_UNVERIFIED", "true")
    s = Settings()  # type: ignore[call-arg]
    assert s.jwt_allow_unverified is True


def test_key_present_is_fail_closed_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_internal_keys(monkeypatch)
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "/run/secrets/jwt-public.pem")
    monkeypatch.delenv("JWT_ALLOW_UNVERIFIED", raising=False)
    s = Settings()  # type: ignore[call-arg]
    assert s.jwt_allow_unverified is False
    assert s.jwt_public_key_path == "/run/secrets/jwt-public.pem"
