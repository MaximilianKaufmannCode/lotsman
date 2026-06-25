# System Actors

Binding reference for ADR-0002 acceptance decision #2: pinned, deterministic
UUIDv7-style constants per system actor (hand-crafted — true `uuidv7()` lands
in PG17). Audit events produced by automated workers must carry a stable,
human-identifiable `actor_id` so incident triage can distinguish human actions
from system actions in the `audit.events` log.

> **Where they live:** these UUIDs are pinned in the shared kernel
> (`lotsman_shared.actors`) and inserted into `auth.users` by the auth-service
> Alembic migration `0002_seed_system_actors.py`. The two must stay in lockstep.

_Last updated: 2026-06-25_

## Reserved UUIDs

All system actors are inserted into `auth.users` with `role = 'viewer'`,
`is_active = false`, and the sentinel password hash `SYSTEM` (argon2id never
produces this string); `totp_secret_enc` is a single zero byte. They cannot
log in and hold no JWT-issuable sessions.

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

These values are pinned in the auth-service Alembic migration
`0002_seed_system_actors.py` and in the shared kernel (`lotsman_shared.actors`
module, per ADR-0002 §4).

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

System actors are seeded **inside** `alembic upgrade head`, not by an init
script — the `infra/postgres/init/` directory holds only `00-extensions.sql`,
`01-schemas-and-roles.sql`, and `03-set-role-passwords.sh` (there is no
`02-system-actors.sql`). The actors must be inserted *after* `auth.users`
exists, which is why this is a versioned data migration rather than init SQL.

Canonical execution order:

1. `00-extensions.sql` — create extensions
2. `01-schemas-and-roles.sql` — create schemas and roles
3. `alembic upgrade head` (all services, in any order) — the auth-service
   migration `0002_seed_system_actors` inserts the five named actors
   idempotently (`ON CONFLICT (id) DO NOTHING`)
4. `make seed` — *optional.* Loads registry demo data
   (`registry_service.scripts.seed`). It does **not** touch `auth.users` and
   does **not** seed system actors; those already exist after step 3.
