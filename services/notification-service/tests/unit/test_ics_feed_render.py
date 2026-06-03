# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for ICS feed rendering — ADR-0005 §10.

Tests:
  - VEVENT structure: UID stability, SUMMARY, DTSTART.
  - VALARM: trigger derived from pre_notice_days.
  - UID uniqueness across documents.
  - Cache hit returns identical bytes.
  - Cache invalidation clears cached payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from notification_service.api.v1.calendar_feed import (
    _cache_get,
    _cache_set,
    _render_ics,
    _render_ics_raw,
    invalidate_ics_cache,
)


def _make_doc(
    doc_id: uuid.UUID | None = None,
    name: str = "Test Licence",
    expires: str = "2026-08-15",
    pre_notice_days: list[int] | None = None,
) -> dict:
    if doc_id is None:
        doc_id = uuid.uuid4()
    return {
        "id": str(doc_id),
        "name": name,
        "display_name": name,
        "expires_at": expires,
        "pre_notice_days": pre_notice_days or [14],
        "asset_name": "ООО Тест",
        "updated_at": "2026-05-01T12:00:00Z",
    }


NOW = datetime(2026, 5, 8, 10, 0, 0, tzinfo=UTC)
HOST = "lotsman.example.com"


# ---------------------------------------------------------------------------
# _render_ics_raw (no icalendar lib dependency)
# ---------------------------------------------------------------------------


def test_render_ics_raw_contains_vcalendar() -> None:
    docs = [_make_doc()]
    result = _render_ics_raw(docs, HOST, NOW)
    text = result.decode("utf-8")
    assert "BEGIN:VCALENDAR" in text
    assert "END:VCALENDAR" in text


def test_render_ics_raw_uid_stable() -> None:
    doc_id = uuid.uuid4()
    doc = _make_doc(doc_id=doc_id)
    result1 = _render_ics_raw([doc], HOST, NOW)
    result2 = _render_ics_raw([doc], HOST, NOW)
    # UID must be the same across identical inputs.
    uid = f"lotsman-doc-{doc_id}@{HOST}"
    assert uid.encode() in result1
    assert uid.encode() in result2


def test_render_ics_raw_uid_unique_across_docs() -> None:
    id1, id2 = uuid.uuid4(), uuid.uuid4()
    docs = [_make_doc(doc_id=id1), _make_doc(doc_id=id2)]
    result = _render_ics_raw(docs, HOST, NOW)
    text = result.decode("utf-8")
    assert f"lotsman-doc-{id1}@{HOST}" in text
    assert f"lotsman-doc-{id2}@{HOST}" in text


def test_render_ics_raw_valarm_uses_pre_notice_days() -> None:
    doc = _make_doc(pre_notice_days=[30, 14, 7])
    result = _render_ics_raw([doc], HOST, NOW)
    text = result.decode("utf-8")
    assert "BEGIN:VALARM" in text
    assert "END:VALARM" in text
    # Trigger should be 30 days (max of [30, 14, 7]).
    assert "TRIGGER:-P30D" in text


def test_render_ics_raw_valarm_default_14_days() -> None:
    doc = _make_doc(pre_notice_days=[])
    result = _render_ics_raw([doc], HOST, NOW)
    text = result.decode("utf-8")
    assert "TRIGGER:-P14D" in text


def test_render_ics_raw_dtstart_correct() -> None:
    doc = _make_doc(expires="2026-12-31")
    result = _render_ics_raw([doc], HOST, NOW)
    text = result.decode("utf-8")
    assert "DTSTART;VALUE=DATE:20261231" in text
    assert "DTEND;VALUE=DATE:20270101" in text


def test_render_ics_raw_skips_doc_without_expires() -> None:
    doc = _make_doc()
    doc["expires_at"] = None
    result = _render_ics_raw([doc], HOST, NOW)
    text = result.decode("utf-8")
    assert "BEGIN:VEVENT" not in text


def test_render_ics_raw_multiple_docs() -> None:
    docs = [_make_doc(name=f"Doc {i}") for i in range(5)]
    result = _render_ics_raw(docs, HOST, NOW)
    text = result.decode("utf-8")
    assert text.count("BEGIN:VEVENT") == 5
    assert text.count("END:VEVENT") == 5


# ---------------------------------------------------------------------------
# _render_ics (uses icalendar lib if available, falls back to raw)
# ---------------------------------------------------------------------------


def test_render_ics_returns_bytes() -> None:
    docs = [_make_doc()]
    result = _render_ics(docs, HOST, NOW)
    assert isinstance(result, bytes)
    assert len(result) > 0


def test_render_ics_contains_vcalendar() -> None:
    docs = [_make_doc()]
    result = _render_ics(docs, HOST, NOW)
    text = result.decode("utf-8")
    assert "VCALENDAR" in text


def test_render_ics_uid_stable() -> None:
    doc_id = uuid.uuid4()
    doc = _make_doc(doc_id=doc_id)
    result1 = _render_ics([doc], HOST, NOW)
    result2 = _render_ics([doc], HOST, NOW)
    uid_fragment = f"lotsman-doc-{doc_id}"
    assert uid_fragment.encode() in result1
    assert uid_fragment.encode() in result2


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_miss_before_set() -> None:
    invalidate_ics_cache()
    assert _cache_get() is None


def test_cache_hit_after_set() -> None:
    invalidate_ics_cache()
    payload = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR"
    _cache_set(payload, ttl=300)
    assert _cache_get() == payload


def test_cache_invalidate_clears() -> None:
    _cache_set(b"some-content", ttl=300)
    assert _cache_get() is not None
    invalidate_ics_cache()
    assert _cache_get() is None


def test_cache_expired_returns_none() -> None:
    """Cache with TTL=0 should be immediately expired."""
    _cache_set(b"content", ttl=0)
    # TTL=0 → expires_at = monotonic + 0, so it's already past.
    import time

    time.sleep(0.01)  # tiny sleep to ensure monotonic advances
    assert _cache_get() is None
