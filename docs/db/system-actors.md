# System Actors

Binding reference for ADR-0002 acceptance decision #2: named UUIDv7 constants
per system actor. Audit events produced by automated workers must carry a
stable, human-identifiable `actor_id` so incident triage can distinguish
human actions from system actions in the `audit.events` log.

## Reserved UUIDs

All system actors are inserted into `auth.users` with `is_active = false`
and the sentinel password hash `SYSTEM` (argon2id never produces this string).
They cannot log in and hold no JWT-issuable sessions.

| Constant name | UUID | auth.users.email | Purpose |
|---|---|---|---|
| `ACTOR_OUTBOX_DISPATCHER` | `018f4e2a-dead-7000-8000-000000000001` | outbox-dispatcher@system.lotsman | ARQ worker that polls `<schema>.outbox` and XADD-s to Redis Streams |
| `ACTOR_NOTIFICATION_SCHEDULER` | `018f4e2a-dead-7000-8000-000000000002` | notification-scheduler@system.lotsman | ARQ worker that computes and enqueues delivery attempts |
| `ACTOR_AUDIT_RECORDER` | `018f4e2a-dead-7000-8000-000000000003` | audit-recorder@system.lotsman | ARQ consumer that writes rows to `audit.events` |
| `ACTOR_SYSTEM_MIGRATOR` | `018f4e2a-dead-7000-8000-000000000004` | system-migrator@system.lotsman | Identity used in audit rows produced during Alembic migrations |
| `ACTOR_SEED_LOADER` | `018f4e2a-dead-7000-8000-000000000005` | seed-loader@system.lotsman | Identity used during `make seed` (reference data load) |
| _(reserved)_ | `018f4e2a-dead-7000-8000-000000000006` | — | Reserved for future system actor |
| _(reserved)_ | `018f4e2a-dead-7000-8000-000000000007` | — | Reserved for future system actor |

## UUID derivation rationale

True UUIDv7 requires a 48-bit millisecond timestamp prefix. Since UUIDv7 is
not available in PostgreSQL 16 (it arrives in PG17 via `uuidv7()`), and
since these values must be **deterministic** and **pinned** (not generated
randomly at startup), we chose hand-crafted values in the style:

```
018f4e2a-dead-7000-8000-00000000000N
```

- Prefix `018f4e2a` is a plausible UUIDv7 timestamp prefix (May 2026).
- `dead` is a mnemonic for "system actor" — visually distinct in logs.
- Version nibble `7` mimics UUIDv7 format for forward-compatibility.
- Variant bits `8000` conform to RFC 4122 variant 1.
- The trailing `N` is the actor ordinal.

These values are pinned in `infra/postgres/init/02-system-actors.sql` and
in the shared kernel (`lotsman_shared.actors` module, per ADR-0002 §4).

## Shared kernel constants

The `lotsman_shared` Python package (per ADR-0002 §4) exposes these as:

```python
# shared/src/lotsman_shared/actors.py
import uuid

ACTOR_OUTBOX_DISPATCHER    = uuid.UUID("018f4e2a-dead-7000-8000-000000000001")
ACTOR_NOTIFICATION_SCHEDULER = uuid.UUID("018f4e2a-dead-7000-8000-000000000002")
ACTOR_AUDIT_RECORDER       = uuid.UUID("018f4e2a-dead-7000-8000-000000000003")
ACTOR_SYSTEM_MIGRATOR      = uuid.UUID("018f4e2a-dead-7000-8000-000000000004")
ACTOR_SEED_LOADER          = uuid.UUID("018f4e2a-dead-7000-8000-000000000005")
```

Services import these constants from `lotsman_shared.actors`, never hardcode
the UUIDs inline.

## Startup order note

`02-system-actors.sql` is safe to run before Alembic migrations complete
(it guards with an existence check and emits NOTICE if `auth.users` is
missing). The canonical execution order is:

1. `00-extensions.sql` — create extensions
2. `01-schemas-and-roles.sql` — create schemas and roles
3. `alembic upgrade head` (all four services, in any order)
4. `02-system-actors.sql` — insert system actors (or `make seed` which includes this)
