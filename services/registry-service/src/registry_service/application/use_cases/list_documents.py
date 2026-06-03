# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-1 + US-3: List documents (with optional filter, sort, pagination).

Search logic (US-2) is handled here via the q parameter passed to the
repository, which uses pg_trgm for queries >= 3 chars and ILIKE for shorter.
Sorting (US-3) is forwarded as sort/dir parameters to the repository.

v1.23.0: Extended with multi-criteria filter params per registry-filters spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from registry_service.application.dto import DocumentDTO, ListDocumentsQuery
from registry_service.application.ports import Clock, DocumentRepository
from registry_service.domain.errors import FilterConflictError
from registry_service.domain.policies import compute_status


@dataclass(slots=True)
class ListDocuments:
    repo: DocumentRepository
    clock: Clock

    async def execute(self, *, query: ListDocumentsQuery) -> list[DocumentDTO]:
        # Guard: expiry_is_null is mutually exclusive with expiry_from/expiry_to.
        # A document with NULL expiry has no date to compare against a range;
        # combining both conditions is semantically contradictory and the caller
        # must pick one (the API layer surfaces this as HTTP 422).
        if query.expiry_is_null and (query.expiry_from or query.expiry_to):
            raise FilterConflictError

        # v1.25.3 — When caller explicitly asks for archived docs via
        # doc_status, override the soft-delete gate. Without this fix, the
        # repository's `WHERE deleted_at IS NULL` clause kills every archived
        # row (status='archived' implies deleted_at IS NOT NULL by domain
        # invariant), so the user sees an empty list — that's the «архива нет»
        # bug. Asking for archived → must include archived. include_archived
        # remains opt-in for callers that haven't narrowed by doc_status.
        include_archived_effective = query.include_archived
        if query.doc_status and "archived" in query.doc_status:
            include_archived_effective = True

        today: date = self.clock.today()
        documents = await self.repo.list_active(
            # legacy single-value (backward compat)
            asset_id=query.asset_id,
            type_code=query.type_code,
            # new multi-value
            asset_ids=query.asset_ids or None,
            type_codes=query.type_codes or None,
            responsible_user_ids=query.responsible_user_ids or None,
            responsible_is_null=query.responsible_is_null,
            expiry_from=query.expiry_from,
            expiry_to=query.expiry_to,
            expiry_is_null=query.expiry_is_null,
            updated_from=query.updated_from,
            updated_to=query.updated_to,
            doc_status=query.doc_status or None,
            asset_status=query.asset_status or None,
            inn=query.inn,
            number_is_null=query.number_is_null,
            expiry_dates=query.expiry_dates or None,
            custom_fields=query.custom_fields or None,
            custom_field_ranges=query.custom_field_ranges or None,
            q=query.q,
            sort=query.sort,
            dir=query.dir,
            offset=query.offset,
            limit=query.limit,
            include_archived=include_archived_effective,
        )

        results: list[DocumentDTO] = []
        for doc in documents:
            urgency = compute_status(doc.expiry_date, doc.deleted_at, today)
            # Apply urgency-status filter client-side after compute (no DB column for urgency).
            # v1.25.5 — query.status is now a list; matches if urgency is in the set.
            # Empty list ⇒ no urgency-status filter applied.
            if query.status and urgency.value not in query.status:
                continue
            results.append(
                DocumentDTO(
                    id=doc.id,
                    asset_id=doc.asset_id,
                    type_code=doc.type_code,
                    number=doc.number,
                    issue_date=doc.issue_date,
                    expiry_date=doc.expiry_date,
                    responsible_user_id=doc.responsible_user_id,
                    status=doc.status,
                    urgency_status=urgency.value,
                    notes=doc.notes,
                    created_by=doc.created_by,
                    updated_by=doc.updated_by,
                    created_at=doc.created_at,
                    updated_at=doc.updated_at,
                    deleted_at=doc.deleted_at,
                    custom_field_values=doc.custom_field_values,
                )
            )
        return results
