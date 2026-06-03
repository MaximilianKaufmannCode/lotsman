# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""ListMySavedFilters use case — v1.23.0 registry-filters feature."""

from __future__ import annotations

from dataclasses import dataclass

from auth_service.application.dto import ListMySavedFiltersQuery, SavedFilterDTO
from auth_service.application.ports import SavedFilterRepository


@dataclass(slots=True)
class ListMySavedFilters:
    """Return all named filter presets owned by the requesting user.

    Results are ordered by is_default DESC (default preset first), then name ASC.
    The repository is responsible for ordering.
    """

    repo: SavedFilterRepository

    async def execute(self, *, query: ListMySavedFiltersQuery) -> list[SavedFilterDTO]:
        filters = await self.repo.list_for_user(query.user_id)
        return [SavedFilterDTO.from_entity(f) for f in filters]
