# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for calendar subscription share-status lifecycle.

Tests the logic in admin_calendar_subscriptions.py:
  - _attempt_grant: granted path
  - _attempt_grant: failed path (EWS error)
  - _attempt_grant: not_attempted (no ews_config)
  - _attempt_grant: not_attempted (empty user_email)
  - _attempt_revoke: revoked path
  - _attempt_revoke: skipped (no ews_config)

We mock _load_ews_config, grant_calendar_share, revoke_calendar_share, and the
repo + outbox to stay pure unit (no DB).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from notification_service.infrastructure.calendar.ews_share import EwsShareError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACTOR_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_USER_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
_USER_EMAIL = "alice@example.com"
_EWS_CONFIG: dict[str, Any] = {
    "ews_url": "https://mail.example.com/EWS/Exchange.asmx",
    "service_account_login": "CORP\\svc",
    "service_account_password": "s3cret!",
    "target_mailbox": "lotsman@example.com",
    "auth_type": "NTLM",
    "verify_ssl": True,
}


def _make_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.set_share_status = AsyncMock()
    return repo


def _make_outbox() -> AsyncMock:
    outbox = AsyncMock()
    outbox.publish = AsyncMock()
    return outbox


# ---------------------------------------------------------------------------
# _attempt_grant
# ---------------------------------------------------------------------------


class TestAttemptGrant:
    @pytest.mark.asyncio
    async def test_granted_path(self) -> None:
        """grant_calendar_share succeeds → share_status='granted' + event published."""
        from notification_service.api.v1.admin_calendar_subscriptions import _attempt_grant

        repo = _make_repo()
        outbox = _make_outbox()
        db = MagicMock()

        with (
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions._load_ews_config",
                new=AsyncMock(return_value=_EWS_CONFIG),
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.grant_calendar_share",
            ) as mock_grant,
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaCalendarSubscriptionRepository",
                return_value=repo,
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaEventOutbox",
                return_value=outbox,
            ),
        ):
            mock_grant.return_value = None  # sync call, but wrapped in to_thread

            # We need to bypass the asyncio.to_thread wrapping for unit tests.
            with patch("asyncio.to_thread", new=AsyncMock(return_value=None)):
                await _attempt_grant(
                    db=db,
                    actor_id=_ACTOR_ID,
                    user_id=_USER_ID,
                    user_email=_USER_EMAIL,
                )

        repo.set_share_status.assert_awaited_once()
        call_kwargs = repo.set_share_status.call_args.kwargs
        assert call_kwargs["share_status"] == "granted"
        assert call_kwargs["share_error"] is None
        assert isinstance(call_kwargs["share_granted_at"], datetime)
        outbox.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_path(self) -> None:
        """grant_calendar_share raises EwsShareError → share_status='failed' + error stored."""
        from notification_service.api.v1.admin_calendar_subscriptions import _attempt_grant

        repo = _make_repo()
        outbox = _make_outbox()
        db = MagicMock()

        with (
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions._load_ews_config",
                new=AsyncMock(return_value=_EWS_CONFIG),
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaCalendarSubscriptionRepository",
                return_value=repo,
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaEventOutbox",
                return_value=outbox,
            ),
        ):
            err = EwsShareError("EWS permission denied — service account lacks ChangePermission")
            with patch("asyncio.to_thread", new=AsyncMock(side_effect=err)):
                await _attempt_grant(
                    db=db,
                    actor_id=_ACTOR_ID,
                    user_id=_USER_ID,
                    user_email=_USER_EMAIL,
                )

        repo.set_share_status.assert_awaited_once()
        call_kwargs = repo.set_share_status.call_args.kwargs
        assert call_kwargs["share_status"] == "failed"
        assert "denied" in call_kwargs["share_error"]
        # Must NOT re-raise — subscription itself is valid.
        outbox.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_not_attempted_when_no_ews_config(self) -> None:
        """No exchange_calendar channel → share_status='not_attempted'."""
        from notification_service.api.v1.admin_calendar_subscriptions import _attempt_grant

        repo = _make_repo()
        outbox = _make_outbox()
        db = MagicMock()

        with (
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions._load_ews_config",
                new=AsyncMock(return_value=None),  # channel absent
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaCalendarSubscriptionRepository",
                return_value=repo,
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaEventOutbox",
                return_value=outbox,
            ),
        ):
            await _attempt_grant(
                db=db,
                actor_id=_ACTOR_ID,
                user_id=_USER_ID,
                user_email=_USER_EMAIL,
            )

        repo.set_share_status.assert_awaited_once()
        call_kwargs = repo.set_share_status.call_args.kwargs
        assert call_kwargs["share_status"] == "not_attempted"
        outbox.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_not_attempted_when_no_email(self) -> None:
        """Empty user_email → share_status='not_attempted' (cannot call EWS)."""
        from notification_service.api.v1.admin_calendar_subscriptions import _attempt_grant

        repo = _make_repo()
        outbox = _make_outbox()
        db = MagicMock()

        with (
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions._load_ews_config",
                new=AsyncMock(return_value=_EWS_CONFIG),
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaCalendarSubscriptionRepository",
                return_value=repo,
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaEventOutbox",
                return_value=outbox,
            ),
        ):
            await _attempt_grant(
                db=db,
                actor_id=_ACTOR_ID,
                user_id=_USER_ID,
                user_email="",  # empty
            )

        repo.set_share_status.assert_awaited_once()
        call_kwargs = repo.set_share_status.call_args.kwargs
        assert call_kwargs["share_status"] == "not_attempted"


# ---------------------------------------------------------------------------
# _attempt_revoke
# ---------------------------------------------------------------------------


class TestAttemptRevoke:
    @pytest.mark.asyncio
    async def test_revoked_path(self) -> None:
        """revoke_calendar_share succeeds → share_status='revoked' + event published."""
        from notification_service.api.v1.admin_calendar_subscriptions import _attempt_revoke

        repo = _make_repo()
        outbox = _make_outbox()
        db = MagicMock()

        with (
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions._load_ews_config",
                new=AsyncMock(return_value=_EWS_CONFIG),
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.revoke_calendar_share",
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaCalendarSubscriptionRepository",
                return_value=repo,
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaEventOutbox",
                return_value=outbox,
            ),
        ):
            with patch("asyncio.to_thread", new=AsyncMock(return_value=None)):
                await _attempt_revoke(
                    db=db,
                    actor_id=_ACTOR_ID,
                    user_id=_USER_ID,
                    user_email=_USER_EMAIL,
                )

        repo.set_share_status.assert_awaited_once()
        call_kwargs = repo.set_share_status.call_args.kwargs
        assert call_kwargs["share_status"] == "revoked"
        outbox.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_skipped_when_no_ews_config(self) -> None:
        """No exchange_calendar channel → revoke skipped silently."""
        from notification_service.api.v1.admin_calendar_subscriptions import _attempt_revoke

        repo = _make_repo()
        outbox = _make_outbox()
        db = MagicMock()

        with (
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions._load_ews_config",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaCalendarSubscriptionRepository",
                return_value=repo,
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaEventOutbox",
                return_value=outbox,
            ),
        ):
            await _attempt_revoke(
                db=db,
                actor_id=_ACTOR_ID,
                user_id=_USER_ID,
                user_email=_USER_EMAIL,
            )

        # set_share_status still called (to not_attempted) — subscription row is updated.
        repo.set_share_status.assert_awaited_once()
        call_kwargs = repo.set_share_status.call_args.kwargs
        assert call_kwargs["share_status"] == "not_attempted"

    @pytest.mark.asyncio
    async def test_revoke_failed_does_not_raise(self) -> None:
        """EWS revoke failure → share_status='failed', does NOT re-raise."""
        from notification_service.api.v1.admin_calendar_subscriptions import _attempt_revoke

        repo = _make_repo()
        outbox = _make_outbox()
        db = MagicMock()

        with (
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions._load_ews_config",
                new=AsyncMock(return_value=_EWS_CONFIG),
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaCalendarSubscriptionRepository",
                return_value=repo,
            ),
            patch(
                "notification_service.api.v1.admin_calendar_subscriptions.SqlaEventOutbox",
                return_value=outbox,
            ),
        ):
            err = EwsShareError("EWS permission denied")
            with patch("asyncio.to_thread", new=AsyncMock(side_effect=err)):
                # Must not raise — subscription is disabled regardless.
                await _attempt_revoke(
                    db=db,
                    actor_id=_ACTOR_ID,
                    user_id=_USER_ID,
                    user_email=_USER_EMAIL,
                )

        repo.set_share_status.assert_awaited_once()
        call_kwargs = repo.set_share_status.call_args.kwargs
        assert call_kwargs["share_status"] == "failed"
        assert "revoke failed" in call_kwargs["share_error"]
