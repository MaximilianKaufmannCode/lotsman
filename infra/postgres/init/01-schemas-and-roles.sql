-- SPDX-License-Identifier: BUSL-1.1
-- Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

-- 01-schemas-and-roles.sql
-- Runs once as superuser during container first-start, AFTER 00-extensions.sql.
-- Creates the four logical schemas and the four application roles.
-- Each role may only access its own schema; all others are explicitly revoked.
--
-- IMPORTANT: This file grants schema-level and default-privilege permissions only.
-- Table-level GRANTs (SELECT, INSERT, UPDATE, DELETE) are emitted by Alembic
-- migrations inside each service's 0001_initial migration so they stay versioned.
-- The one exception: audit.events UPDATE/DELETE is REVOKED here and must NEVER
-- be re-granted to audit_app.

-- ============================================================
-- Schemas
-- ============================================================

CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS registry;
CREATE SCHEMA IF NOT EXISTS notification;
CREATE SCHEMA IF NOT EXISTS audit;

-- ============================================================
-- Roles
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auth_app') THEN
        CREATE ROLE auth_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'registry_app') THEN
        CREATE ROLE registry_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'notification_app') THEN
        CREATE ROLE notification_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_app') THEN
        CREATE ROLE audit_app LOGIN;
    END IF;
END
$$;

-- ============================================================
-- auth_app: owns auth schema only
-- ============================================================

GRANT USAGE ON SCHEMA auth TO auth_app;

-- Default privileges: every future table/sequence/function created in auth schema
-- by the superuser or migration runner is accessible to auth_app.
ALTER DEFAULT PRIVILEGES IN SCHEMA auth
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO auth_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA auth
    GRANT USAGE, SELECT ON SEQUENCES TO auth_app;

-- Explicitly revoke access to all other schemas
REVOKE ALL ON SCHEMA registry    FROM auth_app;
REVOKE ALL ON SCHEMA notification FROM auth_app;
REVOKE ALL ON SCHEMA audit       FROM auth_app;

-- ============================================================
-- registry_app: owns registry schema only
-- ============================================================

GRANT USAGE ON SCHEMA registry TO registry_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA registry
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO registry_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA registry
    GRANT USAGE, SELECT ON SEQUENCES TO registry_app;

REVOKE ALL ON SCHEMA auth         FROM registry_app;
REVOKE ALL ON SCHEMA notification  FROM registry_app;
REVOKE ALL ON SCHEMA audit         FROM registry_app;

-- ============================================================
-- notification_app: owns notification schema only
-- ============================================================

GRANT USAGE ON SCHEMA notification TO notification_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA notification
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO notification_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA notification
    GRANT USAGE, SELECT ON SEQUENCES TO notification_app;

REVOKE ALL ON SCHEMA auth      FROM notification_app;
REVOKE ALL ON SCHEMA registry  FROM notification_app;
REVOKE ALL ON SCHEMA audit     FROM notification_app;

-- ============================================================
-- audit_app: owns audit schema; INSERT+SELECT only on audit.events
-- ============================================================

GRANT USAGE ON SCHEMA audit TO audit_app;

-- Default: allow SELECT+INSERT on new tables. UPDATE/DELETE are deliberately
-- excluded from defaults for the audit schema — all tables here are append-only.
ALTER DEFAULT PRIVILEGES IN SCHEMA audit
    GRANT SELECT, INSERT ON TABLES TO audit_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA audit
    GRANT USAGE, SELECT ON SEQUENCES TO audit_app;

REVOKE ALL ON SCHEMA auth         FROM audit_app;
REVOKE ALL ON SCHEMA registry     FROM audit_app;
REVOKE ALL ON SCHEMA notification FROM audit_app;

-- Explicit belt-and-suspenders: once audit.events exists, strip UPDATE and DELETE.
-- This runs idempotently; the table may not exist yet on first boot (Alembic
-- creates it), so we guard with a DO block.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'audit' AND table_name = 'events'
    ) THEN
        REVOKE UPDATE, DELETE ON audit.events FROM audit_app;
    END IF;
END
$$;

-- ============================================================
-- Notes on what is NOT done here
-- ============================================================
-- 1. No password is set on application roles. Passwords are injected at runtime
--    via Docker secrets and set by the ops's container-init script.
-- 2. No SUPERUSER, CREATEDB, or CREATEROLE granted to any app role.
-- 3. Table-level grants for existing tables (post-Alembic) are handled by the
--    migration itself via op.execute("GRANT ..."). See each 0001_initial migration.
