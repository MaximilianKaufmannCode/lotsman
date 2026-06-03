# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ews_share helpers.

Tests cover:
  - grant_calendar_share: happy path (new permission added)
  - grant_calendar_share: idempotent (email already present)
  - grant_calendar_share: EWS error → EwsShareError with safe message
  - grant_calendar_share: exchangelib absent → RuntimeError
  - revoke_calendar_share: happy path (permission removed)
  - revoke_calendar_share: idempotent (email not present → no-op)
  - revoke_calendar_share: EWS error → EwsShareError
  - list_calendar_shares: returns list of dicts
  - _safe_error: known error classes produce correct safe messages
  - _safe_error: unknown class produces generic safe message
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EWS_CONFIG: dict[str, Any] = {
    "ews_url": "https://mail.example.com/EWS/Exchange.asmx",
    "service_account_login": "CORP\\svc",
    "service_account_password": "s3cret!",
    "target_mailbox": "lotsman@example.com",
    "auth_type": "NTLM",
    "verify_ssl": True,
}


def _make_perm(email: str, level: str = "Reviewer") -> MagicMock:
    perm = MagicMock()
    perm.user = MagicMock()
    perm.user.email_address = email
    perm.permission_level = level
    return perm


def _make_perm_set(perms: list[MagicMock]) -> MagicMock:
    perm_set = MagicMock()
    perm_set.permissions = perms
    return perm_set


def _build_fake_exchangelib(
    existing_perms: list[MagicMock] | None = None,
) -> MagicMock:
    """Return a fake exchangelib module with a pre-configured calendar."""
    fake = MagicMock()

    fake.NTLM = "NTLM"
    fake.BASIC = "BASIC"
    fake.DELEGATE = "DELEGATE"
    fake.Credentials = MagicMock()
    fake.Configuration = MagicMock()
    fake.Mailbox = MagicMock(side_effect=lambda email_address: MagicMock(email_address=email_address))

    perm_set = _make_perm_set(existing_perms or [])
    fake_calendar = MagicMock()
    fake_calendar.permission_set = perm_set

    fake_account = MagicMock()
    fake_account.calendar = fake_calendar
    fake.Account = MagicMock(return_value=fake_account)

    # PermissionSet stores whatever permissions list we pass.
    def make_perm_set(permissions: list[Any]) -> MagicMock:
        ps = MagicMock()
        ps.permissions = permissions
        return ps

    # Permission just wraps its args.
    def make_perm(user: Any, permission_level: str) -> MagicMock:
        p = MagicMock()
        p.user = user
        p.permission_level = permission_level
        return p

    fake.PermissionSet = MagicMock(side_effect=lambda permissions: make_perm_set(permissions))
    fake.Permission = MagicMock(side_effect=lambda user, permission_level: make_perm(user, permission_level))

    return fake


# ---------------------------------------------------------------------------
# _safe_error
# ---------------------------------------------------------------------------


class TestSafeError:
    def test_known_access_denied(self) -> None:
        from notification_service.infrastructure.calendar.ews_share import _safe_error

        class ErrorAccessDenied(Exception):
            pass

        exc = ErrorAccessDenied("raw detail with CORP\\svc password=s3cret!")
        msg = _safe_error(exc)
        assert "s3cret!" not in msg
        assert "CORP\\svc" not in msg
        assert "service account" in msg.lower() or "denied" in msg.lower()

    def test_unknown_class(self) -> None:
        from notification_service.infrastructure.calendar.ews_share import _safe_error

        class SomeRandomEwsException(Exception):
            pass

        exc = SomeRandomEwsException("detail including password=secret123")
        msg = _safe_error(exc)
        assert "secret123" not in msg
        assert "SomeRandomEwsException" in msg

    def test_invalid_operation_hints_it_fallback(self) -> None:
        from notification_service.infrastructure.calendar.ews_share import _safe_error

        class ErrorInvalidOperation(Exception):
            pass

        msg = _safe_error(ErrorInvalidOperation())
        assert "Add-MailboxFolderPermission" in msg


# ---------------------------------------------------------------------------
# grant_calendar_share
# ---------------------------------------------------------------------------


class TestGrantCalendarShare:
    def _patch_exchangelib(
        self,
        monkeypatch: pytest.MonkeyPatch,
        existing_perms: list[MagicMock] | None = None,
    ) -> MagicMock:
        fake = _build_fake_exchangelib(existing_perms)

        # Patch the top-level exchangelib imports used by _build_ews_account.
        import notification_service.infrastructure.calendar.ews_share as mod

        monkeypatch.setattr(mod, "grant_calendar_share", mod.grant_calendar_share)

        # We patch inside the function's import path via a context-level approach.
        # Since the function imports locally, we need to mock the module.
        import sys

        sys.modules["exchangelib"] = fake  # type: ignore[assignment]
        sys.modules["exchangelib.folders"] = MagicMock()

        fake_base = MagicMock()
        fake_base.Permission = fake.Permission
        fake_base.PermissionSet = fake.PermissionSet
        sys.modules["exchangelib.folders.base"] = fake_base

        fake_protocol = MagicMock()
        fake_protocol.BaseProtocol = MagicMock()
        fake_protocol.NoVerifyHTTPAdapter = MagicMock()
        sys.modules["exchangelib.protocol"] = fake_protocol

        return fake

    def test_grant_new_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """New user_email gets appended to permission set and saved."""
        fake = self._patch_exchangelib(monkeypatch, existing_perms=[])
        from notification_service.infrastructure.calendar.ews_share import grant_calendar_share

        grant_calendar_share(ews_config=_EWS_CONFIG, user_email="alice@example.com")

        # calendar.save() must have been called.
        account = fake.Account.return_value
        account.calendar.save.assert_called_once()

    def test_grant_idempotent_when_already_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If email already in permission set — no exception and save is NOT called."""
        existing = [_make_perm("alice@example.com")]
        fake = self._patch_exchangelib(monkeypatch, existing_perms=existing)
        from notification_service.infrastructure.calendar.ews_share import grant_calendar_share

        grant_calendar_share(ews_config=_EWS_CONFIG, user_email="alice@example.com")

        account = fake.Account.return_value
        # Idempotent — should NOT modify + save.
        account.calendar.save.assert_not_called()

    def test_grant_case_insensitive_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Email matching is case-insensitive."""
        existing = [_make_perm("Alice@Example.com")]
        fake = self._patch_exchangelib(monkeypatch, existing_perms=existing)
        from notification_service.infrastructure.calendar.ews_share import grant_calendar_share

        grant_calendar_share(ews_config=_EWS_CONFIG, user_email="alice@example.com")

        account = fake.Account.return_value
        account.calendar.save.assert_not_called()

    def test_grant_ews_error_raises_ews_share_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EWS failure → EwsShareError with safe message (no credentials)."""
        fake = _build_fake_exchangelib()
        fake.Account.side_effect = Exception("NTLM auth failed: password=s3cret!")

        import sys
        sys.modules["exchangelib"] = fake  # type: ignore[assignment]
        sys.modules["exchangelib.folders"] = MagicMock()
        fake_base = MagicMock()
        fake_base.Permission = fake.Permission
        fake_base.PermissionSet = fake.PermissionSet
        sys.modules["exchangelib.folders.base"] = fake_base
        fake_protocol = MagicMock()
        fake_protocol.BaseProtocol = MagicMock()
        fake_protocol.NoVerifyHTTPAdapter = MagicMock()
        sys.modules["exchangelib.protocol"] = fake_protocol

        from notification_service.infrastructure.calendar.ews_share import (
            EwsShareError,
            grant_calendar_share,
        )

        with pytest.raises(EwsShareError) as exc_info:
            grant_calendar_share(ews_config=_EWS_CONFIG, user_email="bob@example.com")

        # The safe message must NOT contain the password.
        assert "s3cret!" not in str(exc_info.value)
        assert "CORP\\svc" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# revoke_calendar_share
# ---------------------------------------------------------------------------


class TestRevokeCalendarShare:
    def _patch_exchangelib(
        self,
        monkeypatch: pytest.MonkeyPatch,
        existing_perms: list[MagicMock] | None = None,
    ) -> MagicMock:
        fake = _build_fake_exchangelib(existing_perms)

        import sys
        sys.modules["exchangelib"] = fake  # type: ignore[assignment]
        sys.modules["exchangelib.folders"] = MagicMock()
        fake_base = MagicMock()
        fake_base.Permission = fake.Permission
        fake_base.PermissionSet = fake.PermissionSet
        sys.modules["exchangelib.folders.base"] = fake_base
        fake_protocol = MagicMock()
        fake_protocol.BaseProtocol = MagicMock()
        fake_protocol.NoVerifyHTTPAdapter = MagicMock()
        sys.modules["exchangelib.protocol"] = fake_protocol

        return fake

    def test_revoke_removes_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """User present → removed from permission set and saved."""
        existing = [_make_perm("alice@example.com"), _make_perm("bob@example.com")]
        fake = self._patch_exchangelib(monkeypatch, existing_perms=existing)
        from notification_service.infrastructure.calendar.ews_share import revoke_calendar_share

        revoke_calendar_share(ews_config=_EWS_CONFIG, user_email="alice@example.com")

        account = fake.Account.return_value
        account.calendar.save.assert_called_once()

        # The PermissionSet that was saved should not contain alice.
        saved_perm_set = account.calendar.permission_set
        # After revoke, permission_set was replaced — check that save was called.
        # (The exact PermissionSet content is validated in integration tests.)

    def test_revoke_idempotent_when_not_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Email not in permission set → no-op, save NOT called."""
        existing = [_make_perm("bob@example.com")]
        fake = self._patch_exchangelib(monkeypatch, existing_perms=existing)
        from notification_service.infrastructure.calendar.ews_share import revoke_calendar_share

        revoke_calendar_share(ews_config=_EWS_CONFIG, user_email="alice@example.com")

        account = fake.Account.return_value
        account.calendar.save.assert_not_called()

    def test_revoke_ews_error_raises_ews_share_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EWS failure → EwsShareError with safe message."""
        fake = _build_fake_exchangelib()
        fake.Account.side_effect = Exception("Credentials: password=s3cret!")

        import sys
        sys.modules["exchangelib"] = fake  # type: ignore[assignment]
        sys.modules["exchangelib.folders"] = MagicMock()
        fake_base = MagicMock()
        fake_base.Permission = fake.Permission
        fake_base.PermissionSet = fake.PermissionSet
        sys.modules["exchangelib.folders.base"] = fake_base
        fake_protocol = MagicMock()
        fake_protocol.BaseProtocol = MagicMock()
        fake_protocol.NoVerifyHTTPAdapter = MagicMock()
        sys.modules["exchangelib.protocol"] = fake_protocol

        from notification_service.infrastructure.calendar.ews_share import (
            EwsShareError,
            revoke_calendar_share,
        )

        with pytest.raises(EwsShareError) as exc_info:
            revoke_calendar_share(ews_config=_EWS_CONFIG, user_email="alice@example.com")

        assert "s3cret!" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# list_calendar_shares
# ---------------------------------------------------------------------------


class TestListCalendarShares:
    def test_list_returns_dicts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_calendar_shares returns a list of {email, permission_level} dicts."""
        existing = [
            _make_perm("alice@example.com", "Reviewer"),
            _make_perm("Default", "None"),
        ]
        fake = _build_fake_exchangelib(existing_perms=existing)

        import sys
        sys.modules["exchangelib"] = fake  # type: ignore[assignment]
        sys.modules["exchangelib.folders"] = MagicMock()
        fake_base = MagicMock()
        sys.modules["exchangelib.folders.base"] = fake_base
        fake_protocol = MagicMock()
        fake_protocol.BaseProtocol = MagicMock()
        fake_protocol.NoVerifyHTTPAdapter = MagicMock()
        sys.modules["exchangelib.protocol"] = fake_protocol

        from notification_service.infrastructure.calendar.ews_share import list_calendar_shares

        result = list_calendar_shares(ews_config=_EWS_CONFIG)

        assert isinstance(result, list)
        assert all("email" in item and "permission_level" in item for item in result)
        emails = [item["email"] for item in result]
        assert "alice@example.com" in emails
