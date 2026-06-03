# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""EWS calendar driver — wraps exchangelib in asyncio.to_thread().

exchangelib uses synchronous I/O under the hood.  All blocking calls are
wrapped in asyncio.to_thread() to keep the notification-service event loop
responsive.

SECURITY INVARIANTS:
  - Credentials are NEVER included in exception messages or log records.
  - All EWS errors are mapped to typed domain exceptions before propagation.
  - The driver only holds the already-decrypted ExchangeCalendarConfig; it
    never touches ChannelCipher itself.

See ADR-0005 §3, §6, §9, §13.
"""

from __future__ import annotations

import asyncio
import time
import uuid as _uuid_mod
from datetime import UTC, date, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from notification_service.domain.calendar import (
    CalendarEventData,
    CalendarMapping,
    CalendarSyncResult,
    CalendarTestResult,
    OrphanEvent,
)
from notification_service.domain.channels import ExchangeCalendarConfig

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

# The exchangelib import is guarded so that the module loads even if
# exchangelib is not installed (unit tests with mocks) or if EWS is
# unreachable at startup.  The actual Account instantiation happens lazily
# inside _make_account(), which is only called when a driver method is used.
try:
    import exchangelib  # type: ignore[import-untyped]  # noqa: F401
    from exchangelib import (
        IMPERSONATION,
        Account,
        CalendarItem,
        Configuration,
        Credentials,
        EWSDate,
        ExtendedProperty,
    )
    from exchangelib.errors import (  # type: ignore[import-untyped]
        ErrorIrresolvableConflict,
        ErrorMailboxStoreUnavailable,
        UnauthorizedError,
    )

    _EXCHANGELIB_AVAILABLE = True
except ImportError:  # pragma: no cover
    _EXCHANGELIB_AVAILABLE = False


# ---------------------------------------------------------------------------
# Domain exceptions (never embed credentials)
# ---------------------------------------------------------------------------


class EwsDriverError(Exception):
    """Base for EWS driver errors."""


class EwsUnauthorizedError(EwsDriverError):
    """Service-account credentials rejected by EWS."""


class EwsMailboxUnavailableError(EwsDriverError):
    """Target mailbox store is unavailable (transient)."""

    transient = True


class EwsConflictError(EwsDriverError):
    """EWS returned ErrorIrresolvableConflict — stale change_key."""

    transient = True


class EwsTransportError(EwsDriverError):
    """Generic transport / protocol error."""

    transient = True


# ---------------------------------------------------------------------------
# ExtendedProperty subclass for LotsmanMarker
# ---------------------------------------------------------------------------

_LOTSMAN_PROPERTY_SET_ID = "00020329-0000-0000-C000-000000000046"


def _make_extended_property_class() -> type:
    """Return a fresh ExtendedProperty subclass for LotsmanMarker.

    Must be called after exchangelib is confirmed importable.
    """

    class LotsmanMarker(ExtendedProperty):  # type: ignore[misc]
        property_set_id = _LOTSMAN_PROPERTY_SET_ID
        property_name = "LotsmanMarker"
        property_type = "String"

    return LotsmanMarker


_LotsmanMarker: type | None = None


def _get_marker_class() -> type:
    global _LotsmanMarker
    if _LotsmanMarker is None:
        _LotsmanMarker = _make_extended_property_class()
    return _LotsmanMarker


# ---------------------------------------------------------------------------
# EwsCalendarDriver
# ---------------------------------------------------------------------------


class EwsCalendarDriver:
    """CalendarDriver implementation using exchangelib.

    The constructor receives an already-decrypted ExchangeCalendarConfig.
    All blocking exchangelib calls run in asyncio.to_thread().
    """

    def __init__(self, config: ExchangeCalendarConfig) -> None:
        self._config = config
        if not _EXCHANGELIB_AVAILABLE:
            raise RuntimeError(
                "exchangelib is not installed. "
                "Add exchangelib>=5.5.0 to notification-service dependencies."
            )

    # ------------------------------------------------------------------
    # Internal helpers (synchronous — called via to_thread)
    # ------------------------------------------------------------------

    def _make_account(self, mailbox: str) -> Account:
        """Build an exchangelib Account for *mailbox* using IMPERSONATION."""
        cfg = self._config

        if cfg.auth_type == "NTLM":
            from exchangelib import NTLM

            auth_protocol = NTLM
        else:
            from exchangelib import BASIC

            auth_protocol = BASIC

        creds = Credentials(
            username=cfg.service_account_login,
            password=cfg.service_account_password,
        )
        # exchangelib 5.x: Configuration does not accept verify_ssl. To disable
        # cert validation use BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter
        # (only on first .verify_ssl=False; default is True). For our default
        # verify_ssl=True path we just don't customize.
        if not cfg.verify_ssl:
            from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
            BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter

        exch_cfg = Configuration(
            service_endpoint=cfg.ews_url,
            credentials=creds,
            auth_type=auth_protocol,
        )
        # access_type=DELEGATE — для shared mailbox с Full-Access / Send-As permission.
        # IMPERSONATION требует ApplicationImpersonation role у service-account, что
        # требует более широких прав в Exchange. DELEGATE достаточно когда у
        # service-account просто есть delegated access на target mailbox.
        from exchangelib import DELEGATE
        return Account(
            primary_smtp_address=mailbox,
            config=exch_cfg,
            autodiscover=False,
            access_type=DELEGATE,
        )

    def _make_calendar_item(
        self,
        account: Account,
        event_data: CalendarEventData,
    ) -> CalendarItem:
        """Construct a CalendarItem (all-day) with the LotsmanMarker property."""
        MarkerCls = _get_marker_class()
        # Register only once per process — exchangelib raises ValueError on re-register.
        if "lotsman_marker" not in {f.name for f in CalendarItem.FIELDS}:
            CalendarItem.register("lotsman_marker", MarkerCls)

        event_day = event_data.event_date
        end_day = event_day + timedelta(days=1)

        item = CalendarItem(
            account=account,
            folder=account.calendar,
            subject=event_data.subject,
            body=event_data.body,
            start=EWSDate(event_day.year, event_day.month, event_day.day),
            end=EWSDate(end_day.year, end_day.month, end_day.day),
            is_all_day=True,
            reminder_is_set=True,
            reminder_minutes_before_start=0,
        )
        item.lotsman_marker = MarkerCls(event_data.external_marker)
        return item

    def _sync_upsert(
        self,
        mailbox: str,
        mapping: CalendarMapping | None,
        event_data: CalendarEventData,
    ) -> CalendarSyncResult:
        """Synchronous upsert called inside asyncio.to_thread()."""
        account = self._make_account(mailbox)
        MarkerCls = _get_marker_class()
        if "lotsman_marker" not in {f.name for f in CalendarItem.FIELDS}:
            CalendarItem.register("lotsman_marker", MarkerCls)

        if mapping is None:
            # CreateItem
            item = self._make_calendar_item(account, event_data)
            item.save()
            return CalendarSyncResult(
                exchange_item_id=item.id,
                change_key=item.changekey,
                external_marker=event_data.external_marker,
                was_created=True,
            )
        else:
            # UpdateItem using stored change_key for optimistic concurrency.
            item = CalendarItem(
                id=mapping.exchange_item_id,
                changekey=mapping.change_key,
                account=account,
                folder=account.calendar,
                subject=event_data.subject,
                body=event_data.body,
                start=EWSDate(
                    event_data.event_date.year,
                    event_data.event_date.month,
                    event_data.event_date.day,
                ),
                end=EWSDate(
                    (event_data.event_date + timedelta(days=1)).year,
                    (event_data.event_date + timedelta(days=1)).month,
                    (event_data.event_date + timedelta(days=1)).day,
                ),
                is_all_day=True,
                reminder_is_set=True,
                reminder_minutes_before_start=0,
            )
            item.lotsman_marker = MarkerCls(event_data.external_marker)
            item.save(update_fields=["subject", "body", "start", "end", "lotsman_marker"])
            return CalendarSyncResult(
                exchange_item_id=item.id,
                change_key=item.changekey,
                external_marker=event_data.external_marker,
                was_created=False,
            )

    def _sync_delete(self, mailbox: str, mapping: CalendarMapping) -> None:
        """Synchronous delete called inside asyncio.to_thread()."""
        account = self._make_account(mailbox)
        item = CalendarItem(
            id=mapping.exchange_item_id,
            changekey=mapping.change_key,
            account=account,
        )
        item.delete()

    def _sync_test_connection(self, mailbox: str) -> CalendarTestResult:
        """Probe EWS by creating + immediately deleting a test event."""
        start_ms = time.monotonic()
        probe_data = CalendarEventData(
            document_id=_PROBE_DOC_ID,
            notice_offset_days=0,
            event_date=date.today(),
            subject="Лоцман: тест подключения — можно удалить",
            body="Это событие создано в рамках проверки подключения и было сразу удалено.",
            external_marker="lotsman:probe:connection-test",
        )
        try:
            result = self._sync_upsert(mailbox, None, probe_data)
            # Immediately delete.
            probe_mapping = CalendarMapping(
                document_id=_PROBE_DOC_ID,
                notice_offset_days=0,
                exchange_item_id=result.exchange_item_id,
                change_key=result.change_key,
                external_marker=probe_data.external_marker,
                sync_state="synced",
            )
            self._sync_delete(mailbox, probe_mapping)
            latency = (time.monotonic() - start_ms) * 1000
            return CalendarTestResult(
                success=True,
                detail="Connection OK — probe event created and deleted.",
                latency_ms=round(latency, 1),
            )
        except Exception as exc:
            # TEMPORARY: log full traceback for diagnostic — REMOVE after live debug
            log.exception("ews.test_connection_failed", exc_class=type(exc).__name__)
            return CalendarTestResult(
                success=False,
                detail=_safe_error_detail(exc),
            )

    def _sync_find_orphans(self, mailbox: str) -> list[OrphanEvent]:
        """Scan calendar for events with our PropertySetId."""
        MarkerCls = _get_marker_class()
        CalendarItem.register("lotsman_marker", MarkerCls)
        account = self._make_account(mailbox)
        orphans = []
        try:
            items = account.calendar.filter(
                additional_fields=["lotsman_marker"],
            )
            for item in items:
                marker_val = getattr(item, "lotsman_marker", None)
                if marker_val is not None:
                    orphans.append(
                        OrphanEvent(
                            exchange_item_id=item.id,
                            change_key=item.changekey,
                            external_marker=str(marker_val),
                        )
                    )
        except Exception:
            log.exception("ews.find_orphans_failed")
        return orphans

    def _sync_upsert_heartbeat(self, mailbox: str) -> None:
        """Create or update the singleton heartbeat event."""
        from datetime import datetime

        now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        event_data = CalendarEventData(
            document_id=_PROBE_DOC_ID,
            notice_offset_days=0,
            event_date=date.today(),
            subject=f"Лоцман: Sync OK · last update {now_str}",
            body=(
                "Это плановое событие-heartbeat от Лоцмана.\n"
                "Если дата события старше 24 часов — проверьте логи notification-service.\n"
                "Раздел настроек: /admin/channels/exchange_calendar"
            ),
            external_marker="lotsman:heartbeat",
        )
        # Try to find existing heartbeat mapping via calendar scan.
        MarkerCls = _get_marker_class()
        CalendarItem.register("lotsman_marker", MarkerCls)
        account = self._make_account(mailbox)
        existing: Any = None
        try:
            for item in account.calendar.filter(additional_fields=["lotsman_marker"]):
                if getattr(item, "lotsman_marker", None) == "lotsman:heartbeat":
                    existing = item
                    break
        except Exception:
            pass  # Fall through to create.

        if existing is not None:
            mapping = CalendarMapping(
                document_id=_PROBE_DOC_ID,
                notice_offset_days=0,
                exchange_item_id=existing.id,
                change_key=existing.changekey,
                external_marker="lotsman:heartbeat",
                sync_state="synced",
            )
            self._sync_upsert(mailbox, mapping, event_data)
        else:
            self._sync_upsert(mailbox, None, event_data)

    # ------------------------------------------------------------------
    # Public async interface (CalendarDriver Protocol)
    # ------------------------------------------------------------------

    async def upsert_event(
        self,
        *,
        mailbox: str,
        mapping: CalendarMapping | None,
        event_data: CalendarEventData,
    ) -> CalendarSyncResult:
        """Create or update a calendar event (non-blocking via to_thread)."""
        return await asyncio.to_thread(self._sync_upsert, mailbox, mapping, event_data)

    async def delete_event(self, *, mailbox: str, mapping: CalendarMapping) -> None:
        """Delete a calendar event (non-blocking via to_thread)."""
        await asyncio.to_thread(self._sync_delete, mailbox, mapping)

    async def find_orphans(self, *, mailbox: str) -> list[OrphanEvent]:
        """Find orphan events (non-blocking via to_thread)."""
        return await asyncio.to_thread(self._sync_find_orphans, mailbox)

    async def test_connection(self, *, mailbox: str) -> CalendarTestResult:
        """Test EWS connectivity (non-blocking via to_thread)."""
        return await asyncio.to_thread(self._sync_test_connection, mailbox)

    async def upsert_heartbeat(self, *, mailbox: str) -> None:
        """Update or create heartbeat event (non-blocking via to_thread)."""
        await asyncio.to_thread(self._sync_upsert_heartbeat, mailbox)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROBE_DOC_ID = _uuid_mod.UUID("00000000-0000-0000-0000-000000000001")


def _safe_error_detail(exc: Exception) -> str:
    """Convert an EWS exception to a safe string (no credentials).

    We deliberately exclude the exc string from the message for auth failures
    because exchangelib may echo back the username/password in debug info.
    """
    class_name = type(exc).__name__
    if _EXCHANGELIB_AVAILABLE:
        if isinstance(exc, UnauthorizedError):
            return "EWS authentication failed — check service_account_login and password."
        if isinstance(exc, ErrorMailboxStoreUnavailable):
            return "EWS mailbox store is unavailable (transient). Retry later."
        if isinstance(exc, ErrorIrresolvableConflict):
            return "EWS conflict — stale change_key. Event will be re-synced."
    # Generic: include only class name, no exc value (may contain credentials).
    return f"EWS error: {class_name}"


def map_ews_exception(exc: Exception) -> EwsDriverError:
    """Map an exchangelib exception to a typed EwsDriverError."""
    if _EXCHANGELIB_AVAILABLE:
        if isinstance(exc, UnauthorizedError):
            return EwsUnauthorizedError(_safe_error_detail(exc))
        if isinstance(exc, ErrorMailboxStoreUnavailable):
            return EwsMailboxUnavailableError(_safe_error_detail(exc))
        if isinstance(exc, ErrorIrresolvableConflict):
            return EwsConflictError(_safe_error_detail(exc))
    return EwsTransportError(_safe_error_detail(exc))
