# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Fake in-memory implementations of repository and service protocols for unit tests.

These fakes implement the same Protocols defined in application/ports.py
without any I/O. They allow use case tests to run without a real DB or Redis.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from lotsman_shared.envelope import EventEnvelope

from registry_service.domain.entities import (
    Asset,
    Attachment,
    Document,
    DocumentType,
    ExportJob,
)


class FakeClock:
    def __init__(self, fixed_dt: datetime | None = None, fixed_date: date | None = None) -> None:
        self._dt = fixed_dt or datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
        self._date = fixed_date or date(2026, 5, 7)

    def now(self) -> datetime:
        return self._dt

    def today(self) -> date:
        return self._date


class FakeEventOutbox:
    def __init__(self) -> None:
        self.published: list[tuple[EventEnvelope, str]] = []

    async def publish(self, envelope: EventEnvelope, *, topic: str) -> None:
        self.published.append((envelope, topic))


class FakeAssetRepository:
    def __init__(self, assets: list[Asset] | None = None) -> None:
        self._store: dict[uuid.UUID, Asset] = {a.id: a for a in (assets or [])}

    async def get_by_id(self, asset_id: uuid.UUID) -> Asset | None:
        return self._store.get(asset_id)

    async def get_active_by_id(self, asset_id: uuid.UUID) -> Asset | None:
        a = self._store.get(asset_id)
        if a and a.deleted_at is None:
            return a
        return None

    async def list_active(
        self,
        *,
        q: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Asset]:
        assets = [a for a in self._store.values() if a.deleted_at is None]
        if q:
            assets = [a for a in assets if q.lower() in a.name.lower()]
        return sorted(assets, key=lambda a: a.name)[offset : offset + limit]

    async def add(self, asset: Asset) -> None:
        self._store[asset.id] = asset

    async def update(self, asset: Asset) -> None:
        self._store[asset.id] = asset

    async def name_exists_for_active(self, name: str) -> bool:
        return any(a.name == name and a.deleted_at is None for a in self._store.values())

    async def archive_cascade_documents(self, asset_id: uuid.UUID, now: Any) -> int:
        return 0  # no doc store in this fake — tests that need cascade use FakeDocumentRepository

    async def restore_cascade_documents(self, asset_id: uuid.UUID) -> int:
        return 0  # deferred per domain design — restore cascade is a no-op stub


class FakeDocumentRepository:
    def __init__(self, documents: list[Document] | None = None) -> None:
        self._store: dict[uuid.UUID, Document] = {d.id: d for d in (documents or [])}

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        return self._store.get(document_id)

    async def list_active(self, **kwargs: Any) -> list[Document]:
        docs = list(self._store.values())
        if not kwargs.get("include_archived"):
            docs = [d for d in docs if d.deleted_at is None]
        # v1.25.3 — honour doc_status filter so use-case tests can verify
        # include_archived auto-override + doc_status filtering together.
        doc_status = kwargs.get("doc_status")
        if doc_status:
            docs = [d for d in docs if d.status in doc_status]
        # v1.25.6 — «не задано» for № документа (null or empty).
        if kwargs.get("number_is_null"):
            docs = [d for d in docs if not d.number]
        return docs

    async def add(self, document: Document) -> None:
        self._store[document.id] = document

    async def update(self, document: Document) -> None:
        self._store[document.id] = document

    async def bulk_archive(self, document_ids: list[uuid.UUID], now: Any) -> tuple[int, int]:
        archived = 0
        skipped = 0
        for doc_id in document_ids:
            doc = self._store.get(doc_id)
            if doc and doc.deleted_at is None:
                doc.deleted_at = now
                doc.status = "archived"
                archived += 1
            else:
                skipped += 1
        return archived, skipped

    async def get_with_asset(self, document_id: uuid.UUID) -> Document | None:
        return await self.get_by_id(document_id)

    async def count_distinct_values(
        self,
        *,
        field: str,
        q: str | None = None,
        limit: int = 100,
    ) -> tuple[list[tuple[str, int]], int, int]:
        """In-memory implementation: collect distinct values + null_count."""
        from collections import Counter

        active_docs = [d for d in self._store.values() if d.deleted_at is None]

        # Collect values for the requested field; track nulls for null_count.
        raw_values: list[str] = []
        null_count = 0
        for doc in active_docs:
            if field == "number":
                if doc.number:
                    raw_values.append(doc.number)
                else:
                    null_count += 1
            elif field == "type_code":
                if doc.type_code:
                    raw_values.append(doc.type_code)
                else:
                    null_count += 1
            elif field == "expiry_date":
                if doc.expiry_date is not None:
                    raw_values.append(doc.expiry_date.isoformat())
                else:
                    null_count += 1
            # asset_name not resolvable in this fake (no asset store reference)

        if q:
            raw_values = [v for v in raw_values if q.lower() in v.lower()]

        counts = Counter(raw_values)
        total_distinct = len(counts)
        sorted_items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:limit]
        return [(v, c) for v, c in sorted_items], total_distinct, null_count

    async def count_distinct_cf_values(
        self,
        *,
        cf_key: str,
        q: str | None = None,
        limit: int = 100,
    ) -> tuple[list[tuple[str, int]], int, int]:
        """In-memory implementation: collect distinct CF values from stored documents."""
        from collections import Counter

        active_docs = [d for d in self._store.values() if d.deleted_at is None]
        raw_values: list[str] = []
        null_count = 0
        for doc in active_docs:
            val = (doc.custom_field_values or {}).get(cf_key)
            if val is None or val == "":
                null_count += 1
            else:
                raw_values.append(str(val))

        if q:
            raw_values = [v for v in raw_values if q.lower() in v.lower()]

        counts = Counter(raw_values)
        total_distinct = len(counts)
        sorted_items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:limit]
        return [(v, c) for v, c in sorted_items], total_distinct, null_count


class FakeDocumentTypeRepository:
    def __init__(self, types: list[DocumentType] | None = None) -> None:
        self._store: dict[str, DocumentType] = {t.code: t for t in (types or [])}

    async def get_by_code(self, code: str) -> DocumentType | None:
        return self._store.get(code)

    async def get_by_code_for_update(self, code: str) -> DocumentType | None:
        # In-memory fake: no actual locking needed
        return self._store.get(code)

    async def list_all(self) -> list[DocumentType]:
        return sorted(self._store.values(), key=lambda t: t.display_name)

    async def upsert(self, doc_type: DocumentType) -> None:
        self._store[doc_type.code] = doc_type

    async def drop_custom_field_from_documents(self, type_code: str, field_key: str) -> None:
        # No-op in fake — tests that need this track it externally
        pass

    async def count_documents_with_field(self, type_code: str, field_key: str) -> int:
        return 0  # no-op in fake


class FakeAttachmentRepository:
    def __init__(self, attachments: list[Attachment] | None = None) -> None:
        self._store: dict[uuid.UUID, Attachment] = {a.id: a for a in (attachments or [])}

    async def get_by_id(self, attachment_id: uuid.UUID) -> Attachment | None:
        return self._store.get(attachment_id)

    async def list_for_document(self, document_id: uuid.UUID) -> list[Attachment]:
        return [a for a in self._store.values() if a.document_id == document_id]

    async def add(self, attachment: Attachment) -> None:
        self._store[attachment.id] = attachment

    async def delete(self, attachment_id: uuid.UUID) -> None:
        self._store.pop(attachment_id, None)


class FakeExportJobRepository:
    def __init__(self) -> None:
        self._store: dict[uuid.UUID, ExportJob] = {}

    async def get_by_id(self, job_id: uuid.UUID) -> ExportJob | None:
        return self._store.get(job_id)

    async def add(self, job: ExportJob) -> None:
        self._store[job.id] = job

    async def update(self, job: ExportJob) -> None:
        self._store[job.id] = job

    async def list_expired_not_purged(self) -> list[ExportJob]:
        now = datetime.now(tz=UTC)
        return [
            j
            for j in self._store.values()
            if j.expires_at and j.expires_at < now and j.file_path is not None
        ]


class FakeAttachmentStorage:
    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def save(
        self,
        *,
        data: bytes,
        document_id: uuid.UUID,
        attachment_id: uuid.UUID,
        original_filename: str,
    ) -> str:
        path = f"attachments/test/{attachment_id}"
        self.saved[path] = data
        return path

    async def delete(self, storage_path: str) -> None:
        self.deleted.append(storage_path)
        self.saved.pop(storage_path, None)

    def signed_url(self, *, storage_path: str, attachment_id: uuid.UUID, ttl_seconds: int) -> str:
        return f"http://test-cdn/{storage_path}?sig=fake&expires=9999999999"


class FakeMimeSniffer:
    def __init__(self, mime: str = "application/pdf") -> None:
        self._mime = mime

    def sniff(self, data: bytes) -> str:
        return self._mime
