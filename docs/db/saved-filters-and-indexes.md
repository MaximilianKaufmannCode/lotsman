# Saved Filters and Registry Index Plan

Feature: multi-level document registry filtering (v1.23.0)

Status: DRAFT — migrations not yet applied.
Backup taken: `pre-filters-feature-2026-05-26-1028.sql.gz` (157 KB).

---

## Section 1. Table: auth.user_saved_filters

### Purpose

Stores per-user named filter presets for the document registry table. Each
preset encodes a serialised filter state (column filters, sort, search terms)
as a JSONB object. One preset per user can be marked as the default, loaded
automatically on page open.

No soft-delete. Presets have low historical value; hard DELETE is fine.

### Full DDL

```sql
CREATE TABLE auth.user_saved_filters (
    id          UUID         NOT NULL DEFAULT gen_random_uuid(),
    user_id     UUID         NOT NULL,
    name        VARCHAR(100) NOT NULL,
    filter_json JSONB        NOT NULL DEFAULT '{}'::jsonb,
    is_default  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT user_saved_filters_pk
        PRIMARY KEY (id),

    CONSTRAINT user_saved_filters_user_fk
        FOREIGN KEY (user_id)
        REFERENCES auth.users (id)
        ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,

    CONSTRAINT user_saved_filters_name_length_chk
        CHECK (char_length(name) BETWEEN 1 AND 100),

    CONSTRAINT user_saved_filters_json_object_chk
        CHECK (jsonb_typeof(filter_json) = 'object')
);

-- Prevents duplicate preset names within a single user's set.
CREATE UNIQUE INDEX user_saved_filters_user_name_uidx
    ON auth.user_saved_filters (user_id, name);

-- Enforces at most one default preset per user.
CREATE UNIQUE INDEX user_saved_filters_user_default_uidx
    ON auth.user_saved_filters (user_id)
    WHERE is_default = TRUE;

-- Hot query: list all presets for a user, newest first.
CREATE INDEX user_saved_filters_user_created_idx
    ON auth.user_saved_filters (user_id, created_at DESC);

-- updated_at trigger (reuses auth.set_updated_at defined in migration 0001).
CREATE TRIGGER user_saved_filters_set_updated_at
    BEFORE UPDATE ON auth.user_saved_filters
    FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at();
```

### Constraint rationale

| Constraint | Reason |
|---|---|
| `user_saved_filters_user_name_uidx` (UNIQUE) | A user cannot have two presets with the same name. Standard UX invariant. |
| `user_saved_filters_user_default_uidx` (partial UNIQUE WHERE is_default = TRUE) | PostgreSQL enforces at most one TRUE value per user_id without needing an application-layer lock. Partial unique indexes on boolean columns are the canonical PG technique. |
| `user_saved_filters_name_length_chk` (CHECK 1..100) | Mirrors the VARCHAR(100) upper bound at the DB layer; also catches empty strings that VARCHAR alone allows. |
| `user_saved_filters_json_object_chk` (CHECK jsonb_typeof) | Prevents storing a JSON array or scalar, which would break all downstream `@>` containment queries used by filter-preview UI. |
| FK `ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED` | Matches the pattern established by `auth.sessions` (see migration 0001). Deferred allows deleting a user and their presets in the same transaction without ordering concerns. |

### No soft-delete

User-visible filter presets have no audit requirement and no GDPR retention
obligation specific to the preset itself. When a user is deleted, their
presets cascade-delete automatically. Adding `deleted_at` would add index
complexity and application code with no benefit. If the requirement changes,
add `deleted_at` in a future migration following the existing pattern on
`auth.users`.

### updated_at trigger

`auth.set_updated_at()` is already defined in migration `0001_initial_auth_schema`.
This migration attaches a new trigger instance to the new table; no new
function needs to be created.

### GRANT

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON auth.user_saved_filters TO auth_app;
```

`auth_app` already holds DML on all auth-schema tables (pattern from 0001).
The new table must be included explicitly because it is created after the
`DEFAULT PRIVILEGES` grant window.

---

## Section 2. Index plan for registry.documents (advanced filters)

### Existing indexes (do not recreate)

| Index name | DDL summary | Hot query |
|---|---|---|
| `documents_asset_active_idx` | btree(asset_id) WHERE deleted_at IS NULL | document list per asset |
| `documents_expiry_active_idx` | btree(expiry_date) WHERE deleted_at IS NULL AND status='active' | notification scheduler |
| `documents_number_trgm_idx` | GIN(number gin_trgm_ops) WHERE deleted_at IS NULL | fuzzy number search |
| `documents_custom_fields_gin_idx` | GIN(custom_field_values jsonb_path_ops) | custom field containment |

### Filter scenario catalogue

| ID | Filter description | Typical query shape |
|---|---|---|
| S1 | Filter by responsible user | `WHERE responsible_user_id = $1 AND deleted_at IS NULL` |
| S3 | Filter by document type | `WHERE type_code = $1 AND deleted_at IS NULL AND status='active'` |
| S4 | Filter by status | `WHERE status = $1 AND deleted_at IS NULL` |
| S6 | Sort/filter by last-modified date | `WHERE deleted_at IS NULL ORDER BY updated_at DESC` |
| S7 | Filter by custom field value | `WHERE custom_field_values @> $1 AND deleted_at IS NULL` |
| S9 | Filter by asset + responsible (combo) | `WHERE asset_id = $1 AND responsible_user_id = $2 AND deleted_at IS NULL` |
| S3c | Filter by asset + type (combo) | `WHERE asset_id = $1 AND type_code = $2 AND deleted_at IS NULL` |

### Candidate index analysis

---

#### Candidate A — `documents_responsible_active_idx`

```sql
CREATE INDEX CONCURRENTLY documents_responsible_active_idx
    ON registry.documents (responsible_user_id)
    WHERE deleted_at IS NULL;
```

Covers: S1, S9 (partial — still needs asset_id filter handled by existing
`documents_asset_active_idx` or re-check, but responsible lookup is the
driver predicate).

Before (Seq Scan on ~1 k rows):
```
Seq Scan on documents  (cost=0.00..X.XX rows=N)
  Filter: (responsible_user_id = $1 AND deleted_at IS NULL)
```

After (Index Scan):
```
Index Scan using documents_responsible_active_idx on documents
  Index Cond: (responsible_user_id = $1)
```

Classification: **PRE-LAUNCH**

Rationale: "My documents" is a first-class filter in the UI. The column is
optional (NULL for unassigned documents), so many rows will have a NULL value
that the partial index `WHERE deleted_at IS NULL` still includes — but the
btree will skip unassigned documents automatically when queried with `= $1`.
With a 2–4 user team this index will be tiny and essentially free to maintain.

---

#### Candidate B — `documents_type_active_idx`

```sql
CREATE INDEX CONCURRENTLY documents_type_active_idx
    ON registry.documents (type_code)
    WHERE deleted_at IS NULL AND status = 'active';
```

Covers: S3 (type filter on active docs), S4 implicitly (status is baked into
the partial predicate).

Before: Seq Scan with `Filter: (type_code = $1 AND deleted_at IS NULL AND status = 'active')`.

After: Index Scan using `documents_type_active_idx` with `Index Cond: (type_code = $1)`.

Classification: **PRE-LAUNCH**

Rationale: "Filter by document type" will appear in every filter preset. The
partial condition `status = 'active'` narrows the index to the live subset,
matching the most common query shape from the registry grid view. The code
column has low cardinality (~5 catalog values), so the planner will use bitmap
index scan efficiently even before row counts grow.

Note: if a query needs archived documents of a specific type (`status = 'archived'`),
it falls back to Seq Scan on the small archived subset — acceptable at this
scale. Add a separate index without the status predicate only if `pg_stat_user_indexes`
shows that access pattern materialises.

---

#### Candidate C — `documents_updated_idx`

```sql
CREATE INDEX CONCURRENTLY documents_updated_idx
    ON registry.documents (updated_at DESC)
    WHERE deleted_at IS NULL;
```

Covers: S6 (sort by last modified in the activity / audit panel).

Before: Seq Scan + Sort (`ORDER BY updated_at DESC`).

After: Index Scan (already in DESC order — no sort step in plan).

Classification: **WAIT-FOR-METRICS**

Rationale: `updated_at DESC` ordering is useful for an "activity feed" view
or a "recently changed" column sort. However, the registry grid default sort
is by `expiry_date`, not `updated_at`. The existing `documents_expiry_active_idx`
covers the primary landing query. Add `documents_updated_idx` only when
`pg_stat_user_indexes` shows the UI actually requests this sort with
measurable frequency (at least after the first 3 months of v1.23.0 usage).
At <10 k rows the seq scan cost for an occasional sort is negligible.

---

#### Candidate D — `documents_asset_type_idx`

```sql
CREATE INDEX CONCURRENTLY documents_asset_type_idx
    ON registry.documents (asset_id, type_code)
    WHERE deleted_at IS NULL;
```

Covers: S3c (asset + type combination, the most common grid drill-down: "show
contracts for company X").

Before: existing `documents_asset_active_idx` is used but must re-check
`type_code` as a filter — extra rows fetched and re-checked.

After: Index Scan on `(asset_id, type_code)` satisfies both predicates at
index level; zero re-checks.

Classification: **PRE-LAUNCH**

Rationale: In a document registry the natural UX flow is "pick a company,
then filter by type". This two-column partial index is the tightest match for
that access pattern. It also subsumes `documents_asset_active_idx` for this
specific query shape (planner will choose the narrower index). The existing
`documents_asset_active_idx` is kept because it covers queries that do NOT
filter by type — the planner chooses the best fit.

---

#### Composite candidate E — `documents_expiry_type_active_idx` (rejected)

A composite `(expiry_date, type_code)` would only help the notification
scheduler when filtering by type — a rare code path. The existing
`documents_expiry_active_idx` is already optimal for the scheduler. Rejected.

---

### PRE-LAUNCH index summary

The following three indexes are included in the registry migration
(`0007_add_filter_indexes.py`):

| Index | Covers |
|---|---|
| `documents_responsible_active_idx` | S1 / S9 |
| `documents_type_active_idx` | S3 / S4 |
| `documents_asset_type_idx` | S3c |

`documents_updated_idx` (S6) is deferred until `pg_stat_statements` evidence
exists.

---

## Section 3. Rollback plan

### auth.user_saved_filters

```sql
DROP TABLE IF EXISTS auth.user_saved_filters;
```

Safe and complete. Drops the table, its indexes, and the trigger in one
operation. No data in other tables references this table (the FK is from this
table to `auth.users`, not the other way around).

### New registry indexes

Each index can be dropped independently without locking writers:

```sql
DROP INDEX CONCURRENTLY IF EXISTS registry.documents_responsible_active_idx;
DROP INDEX CONCURRENTLY IF EXISTS registry.documents_type_active_idx;
DROP INDEX CONCURRENTLY IF EXISTS registry.documents_asset_type_idx;
```

`DROP INDEX CONCURRENTLY` cannot run inside a transaction block. Run these
statements manually via psql if a rollback is needed outside the Alembic
`downgrade()` path (see migration `0007_add_filter_indexes.py` downgrade
section for the Alembic-managed path, which uses `op.execute` with
`transaction_per_migration = False`).

### Backup restore

If something goes wrong at the data level:

```bash
# On the server
gunzip -c /opt/lotsman/backups/pre-filters-feature-2026-05-26-1028.sql.gz \
  | psql -U postgres lotsman
```

Then re-run `alembic upgrade head` to reapply any subsequently needed
migrations from the clean baseline.

---

## Section 4. Custom field filter performance (S7)

### GIN opclass in use

`documents_custom_fields_gin_idx` was created with `jsonb_path_ops`:

```sql
CREATE INDEX documents_custom_fields_gin_idx
    ON registry.documents USING GIN (custom_field_values jsonb_path_ops);
```

`jsonb_path_ops` supports **only** the containment operator `@>`. It does NOT
support `?` (key existence), `?|`, `?&`, or `->>`-based expressions. The
tradeoff is a smaller, faster index for pure containment queries.

### Query shape recommendation for backend

**Use this form (uses the GIN index):**

```sql
SELECT * FROM registry.documents
WHERE custom_field_values @> '{"jurisdiction": "Hong Kong"}'::jsonb
  AND deleted_at IS NULL;
```

The `@>` operator triggers a GIN index lookup. PostgreSQL checks the index
for documents whose `custom_field_values` contains the specified key-value
pair. Efficient even with hundreds of distinct custom field keys.

**Do NOT use this form (does not use the GIN index):**

```sql
SELECT * FROM registry.documents
WHERE custom_field_values->>'jurisdiction' = 'Hong Kong'
  AND deleted_at IS NULL;
```

`->>` extracts a text value at runtime. PostgreSQL cannot use `jsonb_path_ops`
GIN for this expression. The planner falls back to a Seq Scan (or a BitmapHeap
if another index reduces the scan range). At <10 k rows the difference is
immeasurable, but the `@>` form is the correct long-term pattern and should be
established in the codebase now.

**Constructing the filter in Python (SQLAlchemy 2.x):**

```python
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import cast

# Correct — uses @> and the GIN index
stmt = select(Document).where(
    Document.custom_field_values.contains({"jurisdiction": "Hong Kong"}),
    Document.deleted_at.is_(None),
)
```

`Column.contains()` on a JSONB column emits `@>` in SQLAlchemy 2.x.

### Key-existence queries

If a future filter scenario needs "documents that have the 'jurisdiction' key
set at all" (regardless of value), the `jsonb_path_ops` GIN cannot serve it.
Options at that point:

1. Switch the index to `jsonb_ops` (supports `?`) — larger index, still fast.
2. Add a separate `jsonb_ops` GIN alongside `jsonb_path_ops`.
3. Materialise popular key presence into a generated column.

None of these are needed now. Document as an open question and revisit if
key-existence filtering appears in v1.23.0 requirements.

---

## Assumptions

1. `auth.set_updated_at()` trigger function already exists in the `auth` schema
   (created in migration `0001_initial_auth_schema`). The new migration calls
   it directly without redeclaring the function.

2. The application role is `auth_app` for the auth schema and `registry_app`
   for the registry schema, consistent with grants in migrations 0001 of each
   service.

3. `CREATE INDEX CONCURRENTLY` is used for all new registry indexes. At the
   current row count (<100 rows in prod) this adds no measurable overhead, but
   it establishes the correct pattern for when the table grows, and avoids any
   theoretical lock on concurrent reads during the migration window.

4. `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block. The
   registry migration file sets `transaction_per_migration = False` via an
   `alembic_version` sentinel in the file header and uses `op.execute()`
   directly. See migration `0007_add_filter_indexes.py` for the exact pattern.

5. The `filter_json` column stores the serialised filter state as defined by
   the frontend (TanStack Table column-filters shape). The exact schema is
   owned by the frontend and is not validated at the DB layer beyond
   `jsonb_typeof = 'object'`.

6. No UUIDv7 is used yet (PG17 feature). Both new tables use `gen_random_uuid()`
   (UUIDv4), consistent with all existing tables.

---

---

## Section 5. Asset status enum (per gate decision 2026-05-26)

### Context

Gate for the registry-filters feature (v1.23.0) was confirmed on 2026-05-26.
The product owner confirmed three distinct states for partner companies:
`active`, `liquidating`, `archived`. This section documents the design
decisions for migration `0008_add_asset_status.py`.

### Column definition

```sql
ALTER TABLE registry.assets
    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active';

ALTER TABLE registry.assets
    ADD CONSTRAINT assets_status_check
        CHECK (status IN ('active', 'liquidating', 'archived'));
```

Value semantics:

| Value | Meaning |
|---|---|
| `active` | Partner is operating normally. Default for all new rows. |
| `liquidating` | Partner is in a formal wind-down or liquidation process. Documents may still need tracking during this period. |
| `archived` | Partner is no longer active and is not expected to return. No new documents expected. |

### Backfill strategy

On the day of the gate decision, prod holds 5 rows:
- 3 with `deleted_at IS NULL` (active, not soft-deleted)
- 2 with `deleted_at IS NOT NULL` (soft-deleted)

The `ADD COLUMN … NOT NULL DEFAULT 'active'` sets all rows to `'active'` as
a catalog-only operation (no row rewrite on PG 11+, holds ACCESS EXCLUSIVE
briefly for the catalog update only).

A subsequent `UPDATE … WHERE deleted_at IS NOT NULL` then sets those 2 rows
to `status = 'archived'`.

Rationale for mapping `deleted_at IS NOT NULL` to `'archived'` and not
`'liquidating'`: we have no historical signal to distinguish the two for
existing soft-deleted rows. `'archived'` is the more conservative default
("no longer active"). If a specific row should be `'liquidating'` instead,
an editor corrects it via the normal UI after migration. The migration does
not attempt to guess.

The 3 active rows stay at `'active'`, which the `DEFAULT` already
set correctly. No additional UPDATE needed for them.

### Dual-signal model: `deleted_at` vs `status`

Both columns coexist on `registry.assets`. They serve different purposes and
must NOT be collapsed into one:

| Signal | Purpose | Source of truth for... |
|---|---|---|
| `deleted_at TIMESTAMPTZ NULL` | Soft-delete / recoverable removal | Whether a row is "in the trash". NULL = present; non-NULL = soft-deleted. |
| `status VARCHAR(20)` | Functional / business state of the partner | What the partner is doing right now (operating, winding down, archived). |

Key rules for application code:

1. When an operator **archives** a partner via the UI, the backend MUST set
   both `status = 'archived'` AND `deleted_at = now()` in the same transaction.
   This keeps the two signals consistent and ensures the row disappears from
   the default "active assets" view.

2. When a partner is **restored** from soft-delete (rare), the backend MUST
   reset `deleted_at = NULL` AND evaluate which `status` is appropriate
   (default: `'active'`). It must NOT leave `status = 'archived'` on a
   restored, operational partner.

3. `status = 'liquidating'` is the only state where `deleted_at IS NULL` and
   `status != 'active'` coexist legitimately: the partner is still "present"
   (not deleted) but is functionally winding down.

4. The `assets_status_active_idx` partial index covers `WHERE deleted_at IS NULL`,
   which is the standard non-deleted filter used throughout the codebase.
   Filtering `WHERE status = $1 AND deleted_at IS NULL` uses this index
   efficiently for all three status values.

### Index

```sql
CREATE INDEX assets_status_active_idx
    ON registry.assets (status)
    WHERE deleted_at IS NULL;
```

Serves the registry grid filter "show me assets by status" on the non-deleted
subset. Consistent partial predicate pattern with `assets_name_trgm_idx` and
`assets_name_active_uidx` from migration 0001.

At 5 rows this is instantaneous; no `CONCURRENTLY` needed. The index remains
small as the table grows because soft-deleted rows are excluded from it.

### Future unification (NOT in this release)

The current model has two overlapping "inactive" signals: `status = 'archived'`
and `deleted_at IS NOT NULL`. In a future release it may be worth consolidating:

- Option A: make `deleted_at` the only soft-delete signal and define `status`
  purely as a functional state (no `'archived'` value). Archive action sets
  `deleted_at` only; status stays at `'active'` until recovery or hard delete.
- Option B: deprecate `deleted_at` in favour of a `status = 'deleted'` sentinel
  (adds a fourth value to the enum; requires a data migration).

Both options require a multi-step migration plan (add → backfill → switch reads
→ switch writes → drop old). Flag for the next major schema review. Until then,
the dual-signal model described above is the operative contract.

---

## Open Questions

1. **filter_json schema versioning**: When the UI filter shape changes in a
   future version, existing saved presets may become incompatible. Should
   `filter_json` carry a `"version"` key, and should there be a migration or
   application-layer coercion path? Recommend a `"v": 1` sentinel from day one.

2. **Preset count cap**: Should there be a DB-level limit on presets per user
   (e.g. 20)? Currently there is no such constraint. A CHECK constraint on a
   subquery count is not portable across PG versions and is usually better
   enforced at the application layer. Flag for backend.

3. **`documents_updated_idx` trigger point**: Define the `pg_stat_statements`
   threshold that will trigger adding this index (e.g. >100 calls/day to the
   `updated_at DESC` sort path). Recommend reviewing after 30 days of v1.23.0
   being live on PreProd.
