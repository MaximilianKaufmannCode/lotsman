-- SPDX-License-Identifier: BUSL-1.1
-- Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

-- 00-extensions.sql
-- Runs once against the Lotsman database as the superuser (postgres) during
-- container first-start. Idempotent: all statements use IF NOT EXISTS.
--
-- Extensions:
--   pg_trgm  — trigram similarity search (assets.name, documents.number GIN indexes)
--   citext   — case-insensitive text type (auth.users.email)
--   pgcrypto — gen_random_uuid() for UUIDv4 PKs (until UUIDv7 lands in PG17)
--
-- NOT installed: uuid-ossp (superseded by pgcrypto's gen_random_uuid).
-- NOT installed: pg_partman (deferred; audit partitions managed by migrations for now).

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
