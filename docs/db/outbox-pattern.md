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

The three writer services and their outbox topics. The `topic` column is the
Redis Stream key, set at publish time. How it is set differs per service:
`auth-service` derives it from the envelope `type` as
`f"auth.{type.split('.')[1]}"` (e.g. `auth.user.created.v1` → `auth.user`);
`registry-service` passes explicit constants (`TOPIC_DOCUMENTS = "registry.documents"`,
etc.) or a literal (`topic="registry.preferences"`); `notification-service` stores
the topic on write. These names are the source of truth that `audit-service`
subscribes to.

| Schema | Outbox table | Topics |
|---|---|---|
| `auth` | `auth.outbox` | `auth.user`, `auth.session`, `auth.invitation`, `auth.policy` |
| `registry` | `registry.outbox` | `registry.documents`, `registry.assets`, `registry.document_types`, `registry.imports`, `registry.preferences`, `registry.exports` |
| `notification` | `notification.outbox` | `notification.calendar`, `notification.channel`, `notification.email`, `notification.deliveries`, `notification.prefs` |

The `auth` topics are **singular** (`auth.user`, not `auth.users`); a
plural/singular mismatch silently drops events from the audit log. The audit
consumer's `_ALL_STREAMS` (`recorder.py`) does **not** match this produced set
exactly: it subscribes to `auth.user`, `auth.session`, `auth.invite`, and
`auth.invitation` (note the extra `auth.invite`), but does **not** subscribe to
`auth.policy` — so `auth.policy.violation.v1` events are currently not recorded
in the audit log.

`audit-service` has **no outbox**: it is a terminal sink, never a publisher.

## Dead-letter queue table (reserved, not yet wired)

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

> **Not yet implemented.** These tables exist as ORM models and migrations, but
> nothing currently writes to them. The dispatcher has **no retry counter** and
> **never moves rows to the DLQ** — on a publish error it logs and leaves
> `dispatched_at` NULL so the row is retried on the next poll (see *Publish
> phase* below). The DLQ is reserved for a future operator-driven flow.

## Event envelope format

The `payload` JSONB column holds the canonical event envelope
(`lotsman_shared.envelope.EventEnvelope`, per ADR-0002 §C), serialised with
`model_dump(mode="json")`:

```json
{
  "id": "<uuidv4>",
  "type": "registry.document.created.v1",
  "occurred_at": "2026-05-06T12:34:56.789Z",
  "actor_id": "<uuidv4>",
  "request_id": "req_01HX9Z3YV0PQ",
  "version": 1,
  "payload": { }
}
```

`actor_id` is mandatory. Use constants from `lotsman_shared.actors` for
system-initiated events (e.g. `ACTOR_NOTIFICATION_SCHEDULER`).

## Dispatcher contract (outbox-dispatcher ARQ worker)

The dispatcher is an ARQ background worker, one instance per writer service.
It runs as an ARQ cron job with `run_at_startup=True`, and implements the
following loop. The cron interval differs per service: `auth-service` runs at
seconds `{0, 2, 4, …, 58}` — **every 2 seconds** — while `registry-service`
and `notification-service` run at seconds `{0, 5, 10, …, 55}` — **every 5
seconds**.

### Poll phase

The poll runs in its **own** short transaction that opens, selects, and closes
immediately after `fetchall()` — it does not stay open across the publish loop:

```sql
BEGIN;

SELECT id, topic, payload
FROM <schema>.outbox
WHERE dispatched_at IS NULL
ORDER BY occurred_at
LIMIT 50            -- _BATCH_SIZE
FOR UPDATE SKIP LOCKED;

COMMIT;             -- poll transaction closes here, before publishing
```

`SKIP LOCKED` allows multiple dispatcher replicas to run without blocking each
other (though typically one replica per service suffices). Because the poll
transaction closes right after the `SELECT`, the `FOR UPDATE SKIP LOCKED` row
locks are released before the publish loop begins — they are **not** held until
the rows are acknowledged.

### Publish phase

For each row, the envelope is exploded into **one Redis Stream field per
envelope key**, each JSON-encoded, and trimmed to `MAXLEN ~ 100000`:

```python
stream_fields = {k: json.dumps(v) for k, v in row.payload.items()}
await redis.xadd(row.topic, stream_fields, maxlen=100_000, approximate=True)
```

This field shape is a load-bearing contract: the audit consumer reads the
fields back the same way (`json.loads` per field → `EventEnvelope`). Do not
collapse the envelope into a single `"envelope"` field.

On an `XADD` error the dispatcher **logs the exception and leaves
`dispatched_at` NULL**, so the row is retried on the next poll. There is no
in-memory retry counter and no automatic move to `<schema>.outbox_dlq` (see
*Dead-letter queue table* above).

### Acknowledge phase

This runs in a **separate, later transaction** (a fresh session), opened only
after the publish loop finishes:

```sql
BEGIN;

UPDATE <schema>.outbox
SET dispatched_at = now()
WHERE id = ANY($1::uuid[]);

COMMIT;
```

Only the rows whose `XADD` succeeded are acknowledged: the dispatcher collects
the IDs of successfully-published rows and the UPDATE targets that subset.
Rows whose `XADD` raised are left with `dispatched_at` NULL and are retried on
the next poll — the UPDATE does **not** wait for the entire batch to succeed.
If the process crashes between a successful XADD and the UPDATE, that row will
be polled again on restart — producing a duplicate event. Consumers handle
this via the idempotency check (see below).

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
| Single row fails to `XADD` | Exception logged; `dispatched_at` stays NULL; the row is retried every poll cycle. No automatic DLQ move (see *Dead-letter queue table*). |
| Consumer crashes mid-write | Consumer re-reads from Redis Streams using its consumer group; processes the message again. Idempotency key prevents double-write. |

## Redis Stream retention

Per ADR-0002 §C: ~14-day retention bounded by `MAXLEN ~ 100000` per stream.
This is enforced directly by the dispatcher's `XADD(..., maxlen=100_000,
approximate=True)` — there is no separate trimming job; each publish
approximately trims the stream. A consumer offline long enough to fall behind
that window may miss events. The `audit.events` partitioned table is the
durable archive; a "rebuild projection from audit log" recovery runbook is a
future operational task (not yet authored as an ADR).
