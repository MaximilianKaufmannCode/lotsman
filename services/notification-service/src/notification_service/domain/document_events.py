# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Document lifecycle event → notification mapping (ADR-0011 §D5).

Pure, dependency-free helpers: map a registry integration event to a user-facing
category and build Russian titles/bodies. No I/O — the use case supplies the
document label (number) it fetched and the recipient context.
"""

from __future__ import annotations

from typing import Any

# Categories — keep in sync with domain.notification_prefs.DEFAULT_CATEGORIES.
DOC_CREATED = "doc_created"
DOC_UPDATED = "doc_updated"
DOC_ASSIGNED = "doc_assigned"
DOC_ATTACHMENT = "doc_attachment"
DOC_ARCHIVED = "doc_archived"

_ARCHIVE_EVENTS = {
    "registry.document.archived.v1",
    "registry.document.restored.v1",
    "registry.document.bulk_archived.v1",
}

_CATEGORY_TITLES_RU: dict[str, str] = {
    DOC_CREATED: "Создан документ",
    DOC_UPDATED: "Изменён документ",
    DOC_ASSIGNED: "Назначен ответственный",
    DOC_ATTACHMENT: "Вложения документа",
    DOC_ARCHIVED: "Архивирование документа",
}

_FIELD_LABELS_RU: dict[str, str] = {
    "number": "№ документа",
    "expiry_date": "срок действия",
    "issue_date": "дата выдачи",
    "responsible_user_id": "ответственный",
    "status": "статус",
    "notes": "заметки",
    "asset_id": "компания",
    "type_code": "тип документа",
    "custom_field_values": "доп. поля",
    "attachments": "вложения",
}


def map_category(event_type: str, payload: dict[str, Any]) -> str | None:
    """Map a registry event (type + payload) to a notification category.

    Returns None for events we do not notify on.
    """
    if event_type == "registry.document.created.v1":
        return DOC_CREATED
    if event_type in _ARCHIVE_EVENTS:
        return DOC_ARCHIVED
    if event_type == "registry.document.updated.v1":
        field = payload.get("field")
        if field == "attachments":
            return DOC_ATTACHMENT
        if field == "responsible_user_id":
            return DOC_ASSIGNED
        return DOC_UPDATED
    return None


def field_label(field: str | None) -> str:
    if not field:
        return "поле"
    return _FIELD_LABELS_RU.get(field, field)


def category_title(category: str) -> str:
    return _CATEGORY_TITLES_RU.get(category, "Уведомление")


def build_title(
    category: str, doc_label: str | None, company: str | None = None
) -> str:
    """e.g. 'Изменён документ: ДГ-123 · ООО «Ромашка»'.

    `company` is appended when known so the recipient can tell which company the
    document belongs to — in the in-app feed, the daily digest, and the event
    email headline alike. Falls back to base / 'base: label' when absent.
    """
    base = category_title(category)
    title = f"{base}: {doc_label}" if doc_label else base
    if company:
        title = f"{title} · {company}"
    return title


def summarize_fields(fields: list[str]) -> str:
    """Human RU summary of changed field labels: 'срок действия, статус'."""
    labels: list[str] = []
    seen: set[str] = set()
    for f in fields:
        lbl = field_label(f)
        if lbl not in seen:
            seen.add(lbl)
            labels.append(lbl)
    return ", ".join(labels)


COALESCE_WINDOW_SECONDS = 600  # 10 min — keep in sync with EventNotifier


def coalesce_window(now_epoch: float) -> int:
    """Fixed 10-min bucket index for coalescing/dedup (C2)."""
    return int(now_epoch // COALESCE_WINDOW_SECONDS)


def immediate_dedup_key(event_id: str | None, document_id: object) -> str:
    """Dedup key for an immediate (non-coalesced) event → one row per (user, event, doc)."""
    base = event_id or "noid"
    return f"{base}:{document_id}"


def update_dedup_key(document_id: object, window: int) -> str:
    """Dedup key for a coalesced field-update flush → one row per (user, doc, window)."""
    return f"upd:{document_id}:{window}"


def build_body(category: str, *, fields: list[str] | None = None) -> str:
    """Short RU body line for the notification / email."""
    if category == DOC_UPDATED and fields:
        return f"Изменены поля: {summarize_fields(fields)}."
    if category == DOC_ATTACHMENT:
        return "Изменён состав вложений."
    if category == DOC_ASSIGNED:
        return "Вы назначены ответственным за документ."
    if category == DOC_CREATED:
        return "В реестр добавлен новый документ."
    if category == DOC_ARCHIVED:
        return "Изменён статус архивирования документа."
    return ""
