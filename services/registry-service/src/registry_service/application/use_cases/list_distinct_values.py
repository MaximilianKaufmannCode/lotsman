# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""v1.24.0: List distinct values for a field (used by column-filter autocomplete).

Distinct-values endpoint is read-only and works for:
  - System fields: number, asset_name, type_code
  - Custom fields: cf_<key> where <key> exists in document_types.custom_field_schema

Date columns are explicitly rejected (distinct makes no UX sense for hundreds of dates;
UI should show a DatePicker instead — per design spec §4.3 and §6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from registry_service.application.dto import (
    DistinctValueItem,
    ListDistinctValuesQuery,
    ListDistinctValuesResult,
)
from registry_service.application.ports import DocumentRepository, DocumentTypeRepository
from registry_service.domain.errors import DateFieldDistinctNotSupported, UnknownDistinctField

# Regex for validating custom-field key names — mirrors _CF_KEY_RE in documents.py.
_CF_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# System fields that are valid for distinct-values queries.
# v1.24.9: expiry_date добавлено — пользователи фильтруют через тот же
# текстовый паттерн (multi-select distinct dates + «Не задано»),
# что и для cf_-полей. Старый date-range popover остаётся доступным для
# других системных date-колонок.
_SYSTEM_DISTINCT_FIELDS: frozenset[str] = frozenset(
    {"number", "asset_name", "type_code", "expiry_date"}
)

# Date columns that do NOT support distinct-values (high cardinality; use DatePicker in UI).
_DATE_SYSTEM_FIELDS: frozenset[str] = frozenset(
    {"issue_date", "updated_at", "created_at"}
)


@dataclass(slots=True)
class ListDistinctValues:
    """Return distinct values for a filterable column.

    Ports:
      doc_repo  — DocumentRepository  (count_distinct_values / count_distinct_cf_values)
      type_repo — DocumentTypeRepository  (list_all for schema validation)
    """

    doc_repo: DocumentRepository
    type_repo: DocumentTypeRepository

    async def execute(self, *, query: ListDistinctValuesQuery) -> ListDistinctValuesResult:
        field = query.field

        # --- Guard: explicit date-column rejection ---
        if field in _DATE_SYSTEM_FIELDS:
            raise DateFieldDistinctNotSupported(field)

        null_count = 0
        if field in _SYSTEM_DISTINCT_FIELDS:
            values, total_distinct, null_count = await self.doc_repo.count_distinct_values(
                field=field,
                q=query.q,
                limit=query.limit,
            )
        elif field.startswith("cf_"):
            cf_key = field[3:]  # strip 'cf_' prefix
            if not _CF_KEY_RE.match(cf_key):
                raise UnknownDistinctField(field)
            await self._validate_cf_key(cf_key, original_field=field)
            values, total_distinct, null_count = await self.doc_repo.count_distinct_cf_values(
                cf_key=cf_key,
                q=query.q,
                limit=query.limit,
            )
        else:
            raise UnknownDistinctField(field)

        truncated = len(values) >= query.limit

        return ListDistinctValuesResult(
            field=field,
            values=[DistinctValueItem(value=v, count=c) for v, c in values],
            total_distinct=total_distinct,
            truncated=truncated,
            null_count=null_count,
        )

    async def _validate_cf_key(self, cf_key: str, *, original_field: str) -> None:
        """Raise UnknownDistinctField if cf_key is absent from all document type schemas."""
        all_types = await self.type_repo.list_all()
        for doc_type in all_types:
            for field_def in doc_type.custom_field_schema:
                if field_def.key == cf_key:
                    return
        raise UnknownDistinctField(original_field)
