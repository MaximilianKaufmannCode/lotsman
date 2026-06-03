# SPDX-License-Identifier: BUSL-1.1
# Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"""Redis Streams consumer for audit-service.

Consumer group: audit-recorder
Subscribes to: all *.v1 streams (auth.users, auth.sessions, registry.documents,
               registry.assets, registry.document_types, notification.deliveries)

Design:
  - Uses XREADGROUP with consumer group "audit-recorder" and consumer name from config.
  - On each message: deserialise the EventEnvelope, check idempotency via
    audit.processed_events (TODO: implemented in the audit-history feature), then
    INSERT into audit.events.
  - ACKs the message only after a successful INSERT to guarantee at-least-once.
  - On duplicate (envelope.id already in processed_events): ACK and skip.
  - Pending entries (PEL) older than 60s are claimed and re-processed on restart.

TODO (filled in the audit-history feature):
  - Real INSERT into audit.events via AuditEventRepository adapter.
  - Idempotency table audit.processed_events with TTL cleanup.
  - Metrics: ARQ_JOBS_TOTAL labels for each event type processed.
  - /readyz check: "last event processed within SLA" (configurable threshold).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import redis.asyncio as aioredis
from lotsman_shared.actors import ACTOR_AUDIT_RECORDER
from lotsman_shared.envelope import EventEnvelope
from sqlalchemy.exc import IntegrityError

from audit_service.db.models import AuditEvent
from audit_service.infrastructure.db.session import get_session_factory

# Common entity-id keys to look up in the event payload when the
# `<entity_type>_id` convention doesn't match exactly.
_ENTITY_ID_FALLBACK_KEYS = (
    "target_user_id",
    "user_id",
    "document_id",
    "asset_id",
    "session_id",
    "invitation_id",
)


def _extract_entity(envelope: EventEnvelope) -> tuple[str, uuid.UUID | None]:
    """Derive (entity_type, entity_id) from the envelope.

    Event types are namespaced ``<domain>.<entity>.<verb>.v<n>`` (e.g.
    ``registry.document.updated.v1``). Take part [1] as ``entity_type``;
    look up the entity_id in ``payload['<entity_type>_id']`` or one of the
    common fallback keys.
    """
    parts = envelope.type.split(".")
    entity_type = parts[1] if len(parts) >= 3 else "unknown"

    raw: Any = envelope.payload.get(f"{entity_type}_id")
    if raw is None:
        for key in _ENTITY_ID_FALLBACK_KEYS:
            raw = envelope.payload.get(key)
            if raw is not None:
                break

    if raw is None:
        return entity_type, None
    try:
        return entity_type, uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return entity_type, None

from audit_service.config import Settings

log = logging.getLogger(__name__)

# Streams this consumer subscribes to, per ADR-0002 §C ownership matrix.
#
# FIXED 2026-05-22: stream names were plural ("auth.users", "auth.sessions") but
# the outbox dispatchers in auth-service / registry-service write singular topic
# names ("auth.user", "auth.session"). Result: every auth.* and several registry.*
# events for the lifetime of the service silently bypassed audit.events.
# Names below match the `topic` column of each <service>.outbox table in PG, which
# is the source-of-truth used by the dispatchers.
#
# Note: notification-svc currently writes event_type as the full topic (double-
# prefix bug, e.g. "notification.notification.calendar.sync_succeeded.v1").
# Fix for that lives in notification-svc/outbox/dispatcher.py — separate issue.
_ALL_STREAMS = [
    "auth.user",
    "auth.session",
    "auth.invite",
    "auth.invitation",
    "registry.documents",
    "registry.assets",
    "registry.document_types",
    "registry.imports",
    "registry.preferences",
    "registry.exports",
    # Notification streams — added 2026-05-25 after Phase D fix of
    # notification.outbox double-prefix bug.
    "notification.calendar",
    "notification.channel",
    "notification.email",
    "notification.deliveries",
    "notification.prefs",  # ADR-0011 C4 — per-user notification-preference changes
]


async def _ensure_consumer_groups(redis: aioredis.Redis, group: str, streams: list[str]) -> None:
    """Create consumer groups on all streams if they don't already exist.

    XGROUP CREATE with MKSTREAM creates the stream if absent (safe on cold start).
    $ means "start from new messages"; use 0 to replay from the beginning.
    """
    for stream in streams:
        try:
            await redis.xgroup_create(stream, group, id="$", mkstream=True)
            log.info("consumer_group_created", extra={"stream": stream, "group": group})
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                # Group already exists — normal on restarts.
                pass
            else:
                raise


async def run_consumer_loop(
    redis: aioredis.Redis,
    settings: Settings,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Main consumer loop. Runs until shutdown_event is set (or forever in prod).

    Args:
        redis: Connected aioredis client.
        settings: Service settings (consumer_group, consumer_name, etc.).
        shutdown_event: Optional event for graceful shutdown in tests.
    """
    group = settings.consumer_group
    consumer = settings.consumer_name
    streams = settings.stream_keys
    batch = settings.consumer_batch_size
    block_ms = settings.consumer_block_ms

    await _ensure_consumer_groups(redis, group, streams)

    # Build the {stream: ">"} dict for XREADGROUP ("> " means undelivered messages).
    stream_ids: dict[str, str] = {s: ">" for s in streams}

    log.info(
        "audit_consumer_started",
        extra={
            "group": group,
            "consumer": consumer,
            "streams": streams,
            "actor_id": str(ACTOR_AUDIT_RECORDER),
        },
    )

    while shutdown_event is None or not shutdown_event.is_set():
        try:
            results = await redis.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams=stream_ids,  # type: ignore[arg-type]
                count=batch,
                block=block_ms,
                noack=False,
            )
            if not results:
                continue

            for stream_name, messages in results:
                stream_key = stream_name.decode() if isinstance(stream_name, bytes) else stream_name
                for msg_id, fields in messages:
                    await _handle_message(redis, stream_key, msg_id, fields, group)

        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("audit_consumer_error")
            await asyncio.sleep(1)

    log.info("audit_consumer_stopped")


async def _handle_message(
    redis: aioredis.Redis,
    stream: str,
    msg_id: bytes,
    fields: dict[bytes, bytes],
    group: str,
) -> None:
    """Process a single Redis Streams message — persist into audit.events.

    Idempotency: AuditEvent PK is (id, occurred_at). A duplicate envelope.id
    (PEL replay after consumer crash, double-publish, etc.) raises
    IntegrityError on INSERT — we treat that as "already recorded" and ACK.
    Append-only invariant (§4.3) is preserved: no UPDATE / DELETE is ever
    attempted.
    """
    try:
        # Decode fields (Redis Streams values are bytes).
        decoded: dict[str, Any] = {
            (k.decode() if isinstance(k, bytes) else k): (
                json.loads(v.decode() if isinstance(v, bytes) else v)
            )
            for k, v in fields.items()
        }
        envelope = EventEnvelope.model_validate(decoded)
    except Exception:
        log.exception(
            "audit_message_parse_error",
            extra={"stream": stream, "msg_id": str(msg_id)},
        )
        # Do NOT ack — leave in PEL for manual inspection / retry.
        return

    entity_type, entity_id = _extract_entity(envelope)
    if entity_id is None:
        log.warning(
            "audit_event_missing_entity_id",
            extra={
                "stream": stream,
                "envelope_id": str(envelope.id),
                "event_type": envelope.type,
                "payload_keys": list(envelope.payload.keys()),
            },
        )
        # ACK and skip — without entity_id we cannot INSERT (NOT NULL).
        # Surfacing in logs is enough; this is a publisher contract issue.
        await redis.xack(stream, group, msg_id)
        return

    # Carry request_id through from the envelope (publishers add it via
    # make_envelope) — fall back to payload.request_id for older shapes.
    request_id = decoded.get("request_id") or envelope.payload.get("request_id")

    factory = get_session_factory()
    try:
        async with factory() as session:
            async with session.begin():
                row = AuditEvent(
                    id=envelope.id,
                    occurred_at=envelope.occurred_at,
                    actor_id=envelope.actor_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    event_type=envelope.type,
                    payload=decoded,
                    request_id=request_id,
                )
                session.add(row)
                try:
                    await session.flush()
                except IntegrityError:
                    # Duplicate PK (envelope.id, occurred_at) — already
                    # recorded on a previous (possibly crashed) run.
                    await session.rollback()
                    log.info(
                        "audit_event_duplicate_skipped",
                        extra={
                            "stream": stream,
                            "envelope_id": str(envelope.id),
                            "event_type": envelope.type,
                        },
                    )
                    await redis.xack(stream, group, msg_id)
                    return
    except Exception:
        log.exception(
            "audit_event_persist_error",
            extra={
                "stream": stream,
                "envelope_id": str(envelope.id),
                "event_type": envelope.type,
            },
        )
        # Do NOT ack — leave in PEL so it gets retried.
        return

    log.info(
        "audit_event_persisted",
        extra={
            "stream": stream,
            "envelope_id": str(envelope.id),
            "event_type": envelope.type,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
        },
    )
    await redis.xack(stream, group, msg_id)
