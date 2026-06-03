# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ICS calendar feed endpoint — ADR-0005 §10.

GET /api/v1/calendar/feed/{token}.ics

Public endpoint — the token IS the auth (URL-safe bearer credential).
Constant-time comparison guards against timing attacks.

Design decisions:
  - One VEVENT per document with a VALARM (1-event-with-VALARM approach per ADR-0005 §10).
  - In-memory cache (TTL = IcsFeedConfig.cache_ttl_seconds, default 300 s).
    Cache is invalidated by RegistryDocumentConsumer on any document event.
  - Bypass BFF auth-stack: endpoint is in notification-service; nginx proxies
    /api/v1/calendar/feed/* directly → notification-svc:8000, skipping web-bff.
    This keeps ICS accessible even if web-bff is restarted.
    Documented choice: NGINX rule in infra/compose.dev.yml handles this path.

Counter: ics_feed_requests_total (Prometheus).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import Counter

from notification_service.api.deps import DbSession
from notification_service.infrastructure.channel_crypto import ChannelCipher
from notification_service.infrastructure.db.repositories import SqlaCredentialRepository

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar-feed"])

_ICS_REQUESTS = Counter(
    "ics_feed_requests_total",
    "Total ICS feed requests",
    ["status"],
)

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache_lock = asyncio.Lock()
_cache_payload: bytes | None = None
_cache_expires_at: float = 0.0
_cache_ttl: int = 300  # overridden per IcsFeedConfig


def _cache_set(payload: bytes, ttl: int) -> None:
    global _cache_payload, _cache_expires_at, _cache_ttl
    _cache_payload = payload
    _cache_expires_at = time.monotonic() + ttl
    _cache_ttl = ttl


def _cache_get() -> bytes | None:
    if _cache_payload is None:
        return None
    if time.monotonic() > _cache_expires_at:
        return None
    return _cache_payload


def invalidate_ics_cache() -> None:
    """Called by RegistryDocumentConsumer on any document event."""
    global _cache_payload, _cache_expires_at
    _cache_payload = None
    _cache_expires_at = 0.0


# ---------------------------------------------------------------------------
# Helper: constant-time token comparison
# ---------------------------------------------------------------------------


def _tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(a.encode()).digest(),
        hashlib.sha256(b.encode()).digest(),
    )


# ---------------------------------------------------------------------------
# ICS rendering
# ---------------------------------------------------------------------------


def _escape_ics(text: str) -> str:
    """Escape special characters for iCalendar text values per RFC 5545 §3.3.11."""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _ics_datetime(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _render_ics(
    documents: list[dict[str, Any]],
    host: str,
    now: datetime,
) -> bytes:
    """Render RFC 5545 iCalendar content for all active documents."""
    try:
        from icalendar import Calendar, Event, vText
        from icalendar.cal import Alarm

        cal = Calendar()  # type: ignore[no-untyped-call]
        cal.add("prodid", "-//Лоцман//notification-service//RU")
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")
        cal.add("method", "PUBLISH")
        cal.add("x-wr-calname", "Лоцман — сроки документов")

        for doc in documents:
            doc_id = str(doc.get("id") or doc.get("document_id", ""))
            expires_raw = doc.get("expires_at") or doc.get("expiry_date")
            if not expires_raw:
                continue
            if isinstance(expires_raw, str):
                expires_at = date.fromisoformat(expires_raw[:10])
            elif isinstance(expires_raw, date):
                expires_at = expires_raw
            else:
                continue

            doc_name = doc.get("display_name") or doc.get("name") or "Документ"
            asset_name = doc.get("asset_name") or ""
            updated_raw = doc.get("updated_at")
            if isinstance(updated_raw, str):
                updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
            elif isinstance(updated_raw, datetime):
                updated_at = updated_raw
            else:
                updated_at = now

            event = Event()  # type: ignore[no-untyped-call]
            uid = f"lotsman-doc-{doc_id}@{host}"
            event.add("uid", uid)
            event.add("summary", vText(f"Лоцман: {doc_name} истекает"))
            event.add("dtstart", expires_at)
            event.add("dtend", expires_at + timedelta(days=1))
            event.add("dtstamp", now)
            event.add("last-modified", updated_at.replace(tzinfo=None))

            body_parts = [f"Документ: {doc_name}"]
            if asset_name:
                body_parts.append(f"Контрагент: {asset_name}")
            body_parts.append(f"Срок: {expires_at.isoformat()}")
            event.add("description", vText("\n".join(body_parts)))

            # VALARM: remind at max pre_notice_days before expiry.
            pre_notice_days = doc.get("pre_notice_days") or []
            alarm_days = max(pre_notice_days) if pre_notice_days else 14
            alarm = Alarm()  # type: ignore[no-untyped-call]
            alarm.add("action", "DISPLAY")
            alarm.add("description", vText(f"Лоцман: {doc_name} истекает через {alarm_days} дней"))
            alarm.add("trigger", -timedelta(days=alarm_days))
            event.add_component(alarm)

            cal.add_component(event)

        result: bytes = cal.to_ical()
        return result

    except ImportError:
        # icalendar not installed — fall back to raw RFC 5545.
        return _render_ics_raw(documents, host, now)


def _render_ics_raw(
    documents: list[dict[str, Any]],
    host: str,
    now: datetime,
) -> bytes:
    """Minimal RFC 5545 fallback if icalendar lib is unavailable."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Лоцман//notification-service//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Лоцман — сроки документов",
    ]
    now_str = _ics_datetime(now)
    for doc in documents:
        doc_id = str(doc.get("id") or doc.get("document_id", ""))
        expires_raw = doc.get("expires_at") or doc.get("expiry_date")
        if not expires_raw:
            continue
        if isinstance(expires_raw, str):
            expires_at = date.fromisoformat(expires_raw[:10])
        elif isinstance(expires_raw, date):
            expires_at = expires_raw
        else:
            continue

        doc_name = doc.get("display_name") or doc.get("name") or "Документ"
        end_date = expires_at + timedelta(days=1)
        uid = f"lotsman-doc-{doc_id}@{host}"
        pre_notice_days = doc.get("pre_notice_days") or []
        alarm_days = max(pre_notice_days) if pre_notice_days else 14

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{_escape_ics(f'Лоцман: {doc_name} истекает')}",
            f"DTSTART;VALUE=DATE:{_ics_date(expires_at)}",
            f"DTEND;VALUE=DATE:{_ics_date(end_date)}",
            f"DTSTAMP:{now_str}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape_ics(f'Лоцман: {doc_name} истекает через {alarm_days} дней')}",
            f"TRIGGER:-P{alarm_days}D",
            "END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

_cipher = ChannelCipher()


@router.get("/feed/{token_path}", include_in_schema=False)
async def calendar_feed(
    token_path: str,
    request: Request,
    db: DbSession,
) -> Response:
    """Public ICS feed — token embedded in path IS the auth credential.

    Path pattern: /api/v1/calendar/feed/{token}.ics
    The .ics suffix is optional — we strip it when present.

    Two token resolution paths (tried in order):
    1. Per-user calendar_subscriptions.ics_feed_token (preferred — cheap,
       no separate channel config, automatically active for every
       enabled subscriber).
    2. Tenant-wide provider_credentials channel='ics_feed' (legacy).
    """
    from notification_service.infrastructure.db.repositories import (
        SqlaCalendarSubscriptionRepository,
    )

    # Strip .ics suffix.
    token = token_path.removesuffix(".ics")
    cache_ttl = 300  # default 5 min

    async with db.begin():
        sub_repo = SqlaCalendarSubscriptionRepository(db)
        per_user = await sub_repo.get_by_ics_token(token)

    if per_user is not None and per_user.enabled:
        # Per-user feed — authorised. Skip the tenant-wide channel check.
        log.debug("ics_feed.per_user_match", user_id=str(per_user.user_id))
    else:
        # Fall back to tenant-wide ics_feed channel config.
        async with db.begin():
            repo = SqlaCredentialRepository(db)
            all_creds = await repo.get_all()

        ics_row = next((r for r in all_creds if r.channel == "ics_feed"), None)
        if ics_row is None or not ics_row.enabled:
            _ICS_REQUESTS.labels(status="404").inc()
            raise HTTPException(status_code=404, detail="ICS feed not configured")

        try:
            config = _cipher.decrypt(ics_row.config_enc)
            stored_token: str = config.get("token", "")
            cache_ttl = int(config.get("cache_ttl_seconds", 300))
        except Exception as exc:
            _ICS_REQUESTS.labels(status="500").inc()
            raise HTTPException(status_code=500, detail="Feed configuration error") from exc

        if not stored_token or not _tokens_equal(token, stored_token):
            _ICS_REQUESTS.labels(status="401").inc()
            raise HTTPException(status_code=401, detail="Invalid feed token")

    # 2. Try cache.
    async with _cache_lock:
        cached = _cache_get()
        if cached is not None:
            _ICS_REQUESTS.labels(status="200_cached").inc()
            return Response(
                content=cached,
                media_type="text/calendar; charset=utf-8",
                headers={
                    "Content-Disposition": 'inline; filename="lotsman-deadlines.ics"',
                    "Cache-Control": f"max-age={cache_ttl}",
                },
            )

    # 3. Load documents from app.state (registry gateway).
    try:
        registry = request.app.state.registry_gateway
        documents: list[dict[str, Any]] = await registry.list_active_documents()
    except AttributeError:
        # registry_gateway not wired (e.g. in tests without full app).
        documents = []
    except Exception as exc:
        log.error("ics_feed.registry_fetch_failed", error=str(exc))
        _ICS_REQUESTS.labels(status="502").inc()
        raise HTTPException(
            status_code=502,
            detail="Could not load documents from registry",
        ) from exc

    # 4. Render ICS.
    host = request.headers.get("host", "lotsman.example.com")
    now = datetime.now(tz=UTC)
    ics_bytes = _render_ics(documents, host, now)

    # 5. Store in cache.
    async with _cache_lock:
        _cache_set(ics_bytes, cache_ttl)

    _ICS_REQUESTS.labels(status="200").inc()
    return Response(
        content=ics_bytes,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'inline; filename="lotsman-deadlines.ics"',
            "Cache-Control": f"max-age={cache_ttl}",
        },
    )
