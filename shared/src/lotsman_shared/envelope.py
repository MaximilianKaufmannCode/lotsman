# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Canonical event envelope for Лоцман inter-service events.

Matches ADR-0002 §C exactly. Every event published via the transactional
outbox must be serialised as an EventEnvelope before being stored in
<schema>.outbox.payload and forwarded to Redis Streams.

Example::

    from lotsman_shared.envelope import make_envelope

    env = make_envelope(
        event_type="registry.document.created.v1",
        actor_id=current_user_id,
        payload={"document_id": str(doc.id), "asset_id": str(doc.asset_id)},
        request_id=request_id_from_context,
    )
    # Serialise and store:
    outbox_row.payload = env.model_dump(mode="json")
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Canonical v1 event envelope.

    Fields are frozen once created; use make_envelope() for construction.
    """

    model_config = {"frozen": True}

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    """Unique envelope identifier. Consumers use this for idempotency checks."""

    type: str
    """Namespaced event type, e.g. 'registry.document.created.v1'."""

    occurred_at: datetime
    """UTC timestamp of when the event occurred (set by the publisher)."""

    actor_id: uuid.UUID
    """UUID of the acting user or system actor (see lotsman_shared.actors)."""

    payload: dict[str, Any]
    """Type-specific payload, frozen per schema_version."""

    request_id: str | None = None
    """Propagated X-Request-Id from the originating HTTP request, for tracing."""

    version: int = 1
    """Envelope schema version.

    Increment only on breaking changes; dual-publish during migration.
    """


def make_envelope(
    *,
    event_type: str,
    actor_id: uuid.UUID,
    payload: dict[str, Any],
    request_id: str | None = None,
    occurred_at: datetime | None = None,
    envelope_id: uuid.UUID | None = None,
    version: int = 1,
) -> EventEnvelope:
    """Construct an EventEnvelope with sensible defaults.

    Args:
        event_type: Namespaced event type string, e.g. ``'registry.document.created.v1'``.
        actor_id: UUID of the acting user or system actor.
        payload: Type-specific payload dict.
        request_id: Optional trace id from the inbound HTTP request.
        occurred_at: Optional explicit timestamp; defaults to ``datetime.now(tz=timezone.utc)``.
        envelope_id: Optional explicit UUID; defaults to a new uuid4.
        version: Envelope schema version; defaults to 1.

    Returns:
        A frozen :class:`EventEnvelope` instance.
    """
    return EventEnvelope(
        id=envelope_id if envelope_id is not None else uuid.uuid4(),
        type=event_type,
        occurred_at=occurred_at if occurred_at is not None else datetime.now(tz=UTC),
        actor_id=actor_id,
        payload=payload,
        request_id=request_id,
        version=version,
    )
