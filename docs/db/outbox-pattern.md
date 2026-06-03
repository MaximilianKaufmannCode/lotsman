# Transactional Outbox Pattern

Every service that publishes domain events uses the **transactional outbox
pattern** to guarantee that DB mutations and event publications are atomic
without requiring a two-phase commit between Postgres and Redis.

## Problem it solves

Without the outbox pattern:

```
1. INSERT INTO registry.documents       -- succeeds
2. XADD registry.documents * envelope  -- Redis is down
   => document exists, event lost
```

Or in the opposite order:

```
1. XADD registry.documents * envelope  -- succeeds
2. INSERT INTO registry.documents      -- postgres crashes
   => event published for a document that doesn't exist
```

Both scenarios corrupt the audit log and leave notification-service in an
inconsistent state.

## Outbox table shape

Every writer service has its own outbox table in its own schema:

```sql
CREATE TABLE <schema>.outbox (
    id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at TIMESTAMPTZ,               -- NULL = not yet dispatched
    topic         TEXT        NOT NULL,      -- Redis Stream key
    payload       JSONB       NOT NULL,      -- full canonical event envelope
    PRIMARY KEY (id)
);

CREATE INDEX outbox_undispatched_idx
    ON <schema>.outbox (occurred_at)
    WHERE dispatched_at IS NULL;
-- Serves: SELECT ... FOR UPDATE SKIP LOCKED WHERE dispatched_at IS NULL
```

The three writer services and their outbox topics:

| Schema | Outbox table | Topics |
|---|---|---|
| `auth` | `auth.outbox` | `auth.users`, `auth.sessions` |
| `registry` | `registry.outbox` | `registry.documents`, `registry.assets`, `registry.document_types` |
| `notification` | `notification.outbox` | `notification.deliveries` |

`audit-service` has **no outbox**: it is a terminal sink, never a publisher.

## Dead-letter queue shape

Each outbox has a companion `<schema>.outbox_dlq` table for permanently failed
rows:

```sql
CREATE TABLE <schema>.outbox_dlq (
    id          UUID        NOT NULL DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL,
    topic       TEXT        NOT NULL,
    payload     JSONB       NOT NULL,
    failed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error  TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id)
);
```

A row moves to DLQ when the dispatcher has exhausted all retry attempts
(configurable, e.g. 5 retries with exponential backoff). DLQ rows are
never retried automatically; they require operator intervention.

## Event envelope format

The `payload` JSONB column must contain the canonical event envelope per
ADR-0002 §C:

```json
{
  "id": "<uuidv4>",
  "type": "registry.document.created.v1",
  "occurred_at": "2026-05-06T12:34:56.789Z",
  "actor_id": "<uuidv4>",
  "request_id": "req_01HX9Z3YV0PQ",
  "schema_version": 1,
  "payload": { }
}
```

`actor_id` is mandatory. Use constants from `lotsman_shared.actors` for
system-initiated events (e.g. `ACTOR_NOTIFICATION_SCHEDULER`).

## Dispatcher contract (outbox-dispatcher ARQ worker)

The dispatcher is an ARQ background worker, one instance per writer service.
It runs every 1 second and implements the following loop:

### Poll phase

```sql
BEGIN;

SELECT id, topic, payload
FROM <schema>.outbox
WHERE dispatched_at IS NULL
ORDER BY occurred_at
LIMIT 100
FOR UPDATE SKIP LOCKED;
```

`SKIP LOCKED` allows multiple dispatcher replicas to run without blocking each
other (though typically one replica per service suffices).

### Publish phase

For each row:

```python
await redis.xadd(row.topic, {"envelope": row.payload.json()})
```

On Redis error: increment retry counter in memory; do not mark `dispatched_at`.
After max retries, write the row to `<schema>.outbox_dlq` and mark
`dispatched_at = now()` on the original row (so it is no longer polled).

### Acknowledge phase

```sql
UPDATE <schema>.outbox
SET dispatched_at = now()
WHERE id = ANY($1::uuid[]);

COMMIT;
```

The UPDATE and COMMIT happen only after all XADD calls in the batch succeed.
If the process crashes between XADD and UPDATE, the row will be polled again
on restart — producing a duplicate event. Consumers handle this via the
idempotency check (see below).

### At-least-once delivery

The dispatcher guarantees at-least-once delivery. Consumers **must not assume
exactly-once**. Every consumer that writes to a DB table (notification
delivery_attempts, audit events) must check whether the envelope `id` has
already been processed before writing:

```sql
-- Consumer-side idempotency check (example for notification-service):
INSERT INTO notification.idempotency (provider, idempotency_key, first_seen_at)
VALUES ('internal', $envelope_id, now())
ON CONFLICT (provider, idempotency_key) DO NOTHING
RETURNING provider;
-- If no row returned: duplicate, skip processing.
```

## Failure modes

| Failure | Behaviour |
|---|---|
| Redis is down, Postgres is up | Rows accumulate in outbox. Delivered when Redis recovers. No data loss. |
| Postgres is down, Redis is up | Use case transaction fails; no outbox row written; no event published. Consistent. |
| Dispatcher crashes mid-batch | Rows with `dispatched_at IS NULL` are re-polled. Consumers see duplicates; idempotency key handles them. |
| Permanent Redis failure | After max retries, rows move to DLQ. Operator must inspect and republish or accept loss. |
| Consumer crashes mid-write | Consumer re-reads from Redis Streams using its consumer group; processes the message again. Idempotency key prevents double-write. |

## Redis Stream retention

Per ADR-0002 §C.3: 14-day retention (`MAXLEN ~ 100000` per stream).
A consumer offline for more than 14 days may miss events. The `audit.events`
partitioned table is the durable archive; a future ADR-0006 will define the
"rebuild projection from audit log" runbook for such recovery scenarios.
