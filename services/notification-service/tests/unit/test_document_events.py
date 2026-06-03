# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Unit tests for document-event → notification mapping (ADR-0011 §D5)."""

from __future__ import annotations

from notification_service.domain import document_events as de


def test_map_category_created_archived() -> None:
    assert de.map_category("registry.document.created.v1", {}) == de.DOC_CREATED
    assert de.map_category("registry.document.archived.v1", {}) == de.DOC_ARCHIVED
    assert de.map_category("registry.document.restored.v1", {}) == de.DOC_ARCHIVED
    assert de.map_category("registry.document.bulk_archived.v1", {}) == de.DOC_ARCHIVED


def test_map_category_updated_variants() -> None:
    assert (
        de.map_category("registry.document.updated.v1", {"field": "attachments"})
        == de.DOC_ATTACHMENT
    )
    assert (
        de.map_category("registry.document.updated.v1", {"field": "responsible_user_id"})
        == de.DOC_ASSIGNED
    )
    assert (
        de.map_category("registry.document.updated.v1", {"field": "expiry_date"})
        == de.DOC_UPDATED
    )


def test_map_category_unknown_is_none() -> None:
    assert de.map_category("registry.asset.created.v1", {}) is None
    assert de.map_category("whatever", {}) is None


def test_build_title_with_and_without_label() -> None:
    assert de.build_title(de.DOC_CREATED, "ДГ-123") == "Создан документ: ДГ-123"
    assert de.build_title(de.DOC_UPDATED, None) == "Изменён документ"


def test_summarize_fields_dedups_and_labels() -> None:
    s = de.summarize_fields(["expiry_date", "status", "expiry_date"])
    assert s == "срок действия, статус"


def test_build_body_updated_lists_fields() -> None:
    assert "срок действия" in de.build_body(de.DOC_UPDATED, fields=["expiry_date"])
    assert de.build_body(de.DOC_ASSIGNED) == "Вы назначены ответственным за документ."


def test_dedup_keys() -> None:
    assert de.immediate_dedup_key("evt-1", "doc-9") == "evt-1:doc-9"
    assert de.immediate_dedup_key(None, "doc-9") == "noid:doc-9"
    assert de.update_dedup_key("doc-9", 42) == "upd:doc-9:42"


def test_coalesce_window_buckets() -> None:
    # 600s buckets: same bucket within a window, different across the boundary.
    assert de.coalesce_window(6000) == 10
    assert de.coalesce_window(6599) == 10  # same bucket
    assert de.coalesce_window(6600) == 11  # next bucket
