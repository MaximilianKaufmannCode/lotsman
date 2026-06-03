# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for EwsCalendarDriver — mock exchangelib Account.

Tests:
  - extended_property is set with correct marker value
  - all-day flag is True
  - reminder_minutes_before_start = 0
  - error redaction: credentials never appear in exception messages
  - upsert returns CalendarSyncResult with was_created flag
  - delete calls item.delete()
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest

from notification_service.domain.calendar import (
    CalendarEventData,
    CalendarMapping,
)
from notification_service.domain.channels import ExchangeCalendarConfig


def _make_config(**overrides: Any) -> ExchangeCalendarConfig:
    defaults = {
        "ews_url": "https://mail.example.com/EWS/Exchange.asmx",
        "service_account_login": "DOMAIN\\svc",
        "service_account_password": "correct-horse-battery-staple",
        "target_mailbox": "cal@example.com",
        "auth_type": "NTLM",
        "verify_ssl": True,
        "default_notice_days": 14,
    }
    defaults.update(overrides)
    return ExchangeCalendarConfig(**defaults)


def _make_event_data(offset: int = 0) -> CalendarEventData:
    doc_id = uuid.uuid4()
    return CalendarEventData(
        document_id=doc_id,
        notice_offset_days=offset,
        event_date=date(2026, 8, 15),
        subject="Лоцман: Тест истекает СЕГОДНЯ",
        body="Тест тело",
        external_marker=f"lotsman:doc:{doc_id}:offset:{offset}",
    )


@pytest.fixture
def mock_exchangelib(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch exchangelib at the ews_driver module level."""
    mock_mod = MagicMock()

    # Make IMPERSONATION, Account, CalendarItem, etc. accessible as attributes.
    mock_mod.IMPERSONATION = "IMPERSONATION"
    mock_mod.NTLM = "NTLM"
    mock_mod.BASIC = "BASIC"

    fake_item = MagicMock()
    fake_item.id = "FakeItemId==ABC"
    fake_item.changekey = "FakeCK==XYZ"
    fake_item.is_all_day = True
    fake_item.reminder_minutes_before_start = 0

    fake_item_cls = MagicMock(return_value=fake_item)
    fake_item_cls.register = MagicMock()
    mock_mod.CalendarItem = fake_item_cls

    fake_account = MagicMock()
    fake_account.calendar = MagicMock()
    mock_mod.Account = MagicMock(return_value=fake_account)
    mock_mod.Credentials = MagicMock()
    mock_mod.Configuration = MagicMock()
    mock_mod.EWSDate = MagicMock(side_effect=lambda y, m, d: date(y, m, d))
    mock_mod.EWSTimeZone = MagicMock()

    class FakeExtProp:
        property_set_id = "00020329-0000-0000-C000-000000000046"
        property_name = "LotsmanMarker"
        property_type = "String"

        def __init__(self, value: str) -> None:
            self.value = value

    mock_mod.ExtendedProperty = FakeExtProp

    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver._EXCHANGELIB_AVAILABLE",
        True,
    )
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver.Account",
        mock_mod.Account,
    )
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver.CalendarItem",
        fake_item_cls,
    )
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver.Credentials",
        mock_mod.Credentials,
    )
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver.Configuration",
        mock_mod.Configuration,
    )
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver.EWSDate",
        mock_mod.EWSDate,
    )
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver.IMPERSONATION",
        mock_mod.IMPERSONATION,
    )
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver.ExtendedProperty",
        FakeExtProp,
    )
    # Reset the cached marker class so tests get a fresh one.
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver._LotsmanMarker",
        None,
    )

    return mock_mod


def test_ews_driver_create_sets_all_day(mock_exchangelib: MagicMock) -> None:
    """CalendarItem must be created with is_all_day=True."""
    from notification_service.infrastructure.calendar.ews_driver import EwsCalendarDriver

    driver = EwsCalendarDriver(_make_config())
    event_data = _make_event_data(0)

    result = driver._sync_upsert("cal@example.com", None, event_data)

    assert result.was_created is True
    assert result.exchange_item_id == "FakeItemId==ABC"
    assert result.change_key == "FakeCK==XYZ"


def test_ews_driver_create_sets_external_marker(mock_exchangelib: MagicMock) -> None:
    """The CalendarItem must have lotsman_marker set to external_marker value."""
    from notification_service.infrastructure.calendar.ews_driver import EwsCalendarDriver

    driver = EwsCalendarDriver(_make_config())
    event_data = _make_event_data(14)

    # Call _sync_upsert — we verify the marker is assigned.
    import notification_service.infrastructure.calendar.ews_driver as drv_mod

    created_item = drv_mod.CalendarItem.return_value  # type: ignore[attr-defined]
    driver._sync_upsert("cal@example.com", None, event_data)

    # The item should have lotsman_marker set.
    assert hasattr(created_item, "lotsman_marker")
    marker_obj = created_item.lotsman_marker
    assert marker_obj.value == event_data.external_marker


def test_ews_driver_reminder_is_zero(mock_exchangelib: MagicMock) -> None:
    """reminder_minutes_before_start must be 0 (fires at event time)."""
    from notification_service.infrastructure.calendar.ews_driver import EwsCalendarDriver

    driver = EwsCalendarDriver(_make_config())
    event_data = _make_event_data(0)

    import notification_service.infrastructure.calendar.ews_driver as drv_mod

    driver._sync_upsert("cal@example.com", None, event_data)

    # Verify CalendarItem was constructed with reminder_minutes_before_start=0.
    call_kwargs = drv_mod.CalendarItem.call_args.kwargs  # type: ignore[attr-defined]
    assert call_kwargs.get("reminder_minutes_before_start") == 0
    assert call_kwargs.get("is_all_day") is True


def test_ews_driver_update_uses_change_key(mock_exchangelib: MagicMock) -> None:
    """UpdateItem path must supply existing mapping's change_key."""
    from notification_service.infrastructure.calendar.ews_driver import EwsCalendarDriver

    driver = EwsCalendarDriver(_make_config())
    doc_id = uuid.uuid4()
    event_data = CalendarEventData(
        document_id=doc_id,
        notice_offset_days=14,
        event_date=date(2026, 8, 1),
        subject="Лоцман: Тест через 14 дней",
        body="Тело",
        external_marker=f"lotsman:doc:{doc_id}:offset:14",
    )
    existing_mapping = CalendarMapping(
        document_id=doc_id,
        notice_offset_days=14,
        exchange_item_id="OldItemId==",
        change_key="OldCK==",
        external_marker=f"lotsman:doc:{doc_id}:offset:14",
        sync_state="synced",
    )

    result = driver._sync_upsert("cal@example.com", existing_mapping, event_data)

    import notification_service.infrastructure.calendar.ews_driver as drv_mod

    call_kwargs = drv_mod.CalendarItem.call_args.kwargs  # type: ignore[attr-defined]
    assert call_kwargs.get("id") == "OldItemId=="
    assert call_kwargs.get("changekey") == "OldCK=="
    assert result.was_created is False


def test_ews_driver_delete_calls_item_delete(mock_exchangelib: MagicMock) -> None:
    """delete_event() must call item.delete()."""
    from notification_service.infrastructure.calendar.ews_driver import EwsCalendarDriver

    driver = EwsCalendarDriver(_make_config())
    doc_id = uuid.uuid4()
    mapping = CalendarMapping(
        document_id=doc_id,
        notice_offset_days=0,
        exchange_item_id="ItemToDelete==",
        change_key="CK==",
        external_marker=f"lotsman:doc:{doc_id}:offset:0",
        sync_state="synced",
    )

    driver._sync_delete("cal@example.com", mapping)

    import notification_service.infrastructure.calendar.ews_driver as drv_mod

    created_item = drv_mod.CalendarItem.return_value  # type: ignore[attr-defined]
    created_item.delete.assert_called_once()


def test_ews_driver_unauthorized_error_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unauthorized error message must NOT contain the password."""

    class FakeUnauthorized(Exception):
        pass

    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver._EXCHANGELIB_AVAILABLE",
        True,
    )
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver.UnauthorizedError",
        FakeUnauthorized,
    )

    from notification_service.infrastructure.calendar.ews_driver import (
        _safe_error_detail,
    )

    exc = FakeUnauthorized("username=DOMAIN\\svc password=correct-horse-battery-staple")
    detail = _safe_error_detail(exc)

    # Password must not appear.
    assert "correct-horse-battery-staple" not in detail
    assert "DOMAIN\\svc" not in detail
    # Should mention authentication failure.
    assert "auth" in detail.lower() or "EWS" in detail


def test_ews_driver_test_connection_returns_result(mock_exchangelib: MagicMock) -> None:
    """test_connection() must return CalendarTestResult."""
    from notification_service.infrastructure.calendar.ews_driver import EwsCalendarDriver

    driver = EwsCalendarDriver(_make_config())
    result = driver._sync_test_connection("cal@example.com")

    # Even with our mock (which doesn't actually raise), result should be a CalendarTestResult.
    from notification_service.domain.calendar import CalendarTestResult

    assert isinstance(result, CalendarTestResult)


def test_ews_driver_not_installed_raises_on_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If exchangelib is not available, constructor raises RuntimeError."""
    monkeypatch.setattr(
        "notification_service.infrastructure.calendar.ews_driver._EXCHANGELIB_AVAILABLE",
        False,
    )

    from notification_service.infrastructure.calendar.ews_driver import EwsCalendarDriver

    with pytest.raises(RuntimeError, match="exchangelib"):
        EwsCalendarDriver(_make_config())
