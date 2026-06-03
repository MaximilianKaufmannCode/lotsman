# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""US-18: Get field-level change history for a document.

Returns events enriched with registry-owned reference data (asset names,
document type display names, attachment filenames). User-name resolution
is left to the web-bff layer (which has access to auth-service via the
internal JWT — registry-svc does not currently have that key).

Response shape per event:

    {
      "id": "<uuid>",
      "occurred_at": "<iso>",
      "actor_id": "<uuid>",            # raw, web-bff resolves to actor_name
      "actor_name": null,              # populated by web-bff
      "event_type": "registry.document.updated.v1",
      "entity_type": "document",
      "entity_id": "<uuid>",
      "field": "expiry_date",          # flattened from envelope.payload.field
      "before": <raw>,                 # envelope.payload.before — null for non-update events
      "after":  <raw>,                 # envelope.payload.after
      "before_display": <str|null>,    # human-readable for asset_id / type_code / attachments
      "after_display":  <str|null>,    # likewise
    }

For the field "responsible_user_id", before/after are user UUIDs — web-bff
fills before_display/after_display in its enrichment pass.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from registry_service.application.ports import (
    AssetRepository,
    AuditServiceClient,
    DocumentTypeRepository,
)


@dataclass(slots=True)
class GetDocumentHistory:
    audit_client: AuditServiceClient
    asset_repo: AssetRepository | None = None
    document_type_repo: DocumentTypeRepository | None = None

    async def execute(
        self,
        *,
        document_id: uuid.UUID,
        limit: int = 50,
        actor_id: uuid.UUID,
        role: str,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raw_events = await self.audit_client.get_events(
            entity_type="document",
            entity_id=document_id,
            limit=limit,
            actor_id=actor_id,
            role=role,
            request_id=request_id,
        )
        return await self._enrich(raw_events)

    async def _enrich(self, raw_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Collect unique asset_ids and type_codes referenced across all events.
        asset_ids: set[uuid.UUID] = set()
        type_codes: set[str] = set()
        for event in raw_events:
            inner = self._inner_payload(event)
            field = inner.get("field")
            for value in (inner.get("before"), inner.get("after")):
                if value is None:
                    continue
                if field == "asset_id":
                    try:
                        asset_ids.add(uuid.UUID(str(value)))
                    except (ValueError, TypeError):
                        pass
                elif field == "type_code" and isinstance(value, str):
                    type_codes.add(value)

        assets: dict[uuid.UUID, str] = {}
        if self.asset_repo is not None:
            for aid in asset_ids:
                asset = await self.asset_repo.get_by_id(aid)
                if asset is not None:
                    assets[aid] = asset.name

        doc_types: dict[str, str] = {}
        if self.document_type_repo is not None:
            for code in type_codes:
                dt = await self.document_type_repo.get_by_code(code)
                if dt is not None:
                    doc_types[code] = dt.display_name

        return [self._flatten(event, assets, doc_types) for event in raw_events]

    @staticmethod
    def _inner_payload(event: dict[str, Any]) -> dict[str, Any]:
        """Drill into envelope-wrapped payload.

        audit.events.payload is the full EventEnvelope dict; envelope.payload is
        the domain event body that holds field/before/after.
        """
        outer = event.get("payload") or {}
        inner = outer.get("payload") if isinstance(outer, dict) else None
        return inner if isinstance(inner, dict) else {}

    def _flatten(
        self,
        event: dict[str, Any],
        assets: dict[uuid.UUID, str],
        doc_types: dict[str, str],
    ) -> dict[str, Any]:
        inner = self._inner_payload(event)
        field = inner.get("field")
        before = inner.get("before")
        after = inner.get("after")
        return {
            "id": event.get("id"),
            "occurred_at": event.get("occurred_at"),
            "actor_id": event.get("actor_id"),
            "actor_name": None,  # web-bff populates
            "event_type": event.get("event_type"),
            "entity_type": event.get("entity_type"),
            "entity_id": event.get("entity_id"),
            "field": field,
            "before": before,
            "after": after,
            "before_display": self._display(field, before, assets, doc_types),
            "after_display": self._display(field, after, assets, doc_types),
        }

    @staticmethod
    def _display(
        field: str | None,
        value: Any,
        assets: dict[uuid.UUID, str],
        doc_types: dict[str, str],
    ) -> str | None:
        """Resolve a raw payload value to a human-readable string where registry owns the lookup.

        - asset_id  → asset.name
        - type_code → document_type.display_name
        - attachments → original_filename (if publisher included it on delete)
        Other fields return None so web-bff / SPA format from raw `before`/`after`
        (numbers, dates, free text).
        """
        if value is None:
            return None
        if field == "asset_id":
            try:
                aid = uuid.UUID(str(value))
            except (ValueError, TypeError):
                return None
            return assets.get(aid)
        if field == "type_code" and isinstance(value, str):
            return doc_types.get(value)
        if field == "attachments" and isinstance(value, dict):
            filename = value.get("original_filename")
            if isinstance(filename, str) and filename:
                return filename
            attachment_id = value.get("attachment_id")
            if attachment_id:
                return f"вложение ({str(attachment_id)[:8]})"
        return None
