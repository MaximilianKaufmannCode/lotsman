-- SPDX-License-Identifier: BUSL-1.1
-- Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

-- One-shot cleanup after failed xlsx-import wizard runs (v1.7.x → v1.8.0).
--
-- Idempotent. Safe to re-run. Run inside a single transaction.
--
-- Usage:
--   docker compose exec -T postgres psql -U postgres -d lotsman \
--     < scripts/cleanup-failed-imports.sql
--
-- What it does:
--   1. Soft-deletes documents of types that the corp-Excel registry
--      template typically uses (business-registration-ordinance,
--      annual-return-nar1, reports-and-financial-statements). The
--      original «Договор» test document is NOT touched.
--   2. Strips the three custom field keys that earlier import attempts
--      leaked into the «Договор» schema due to the f_-key collision
--      bug fixed in v1.7.4.
--   3. Defensively strips those keys from every document's
--      custom_field_values JSONB.

BEGIN;

-- 1. Soft-delete legacy import rows.
WITH soft_deleted AS (
    UPDATE registry.documents
       SET deleted_at = now(),
           updated_at = now()
     WHERE deleted_at IS NULL
       AND type_code IN (
           'reports-and-financial-statements',
           'business-registration-ordinance',
           'annual-return-nar1'
       )
    RETURNING id
)
SELECT count(*) AS docs_soft_deleted FROM soft_deleted;

-- 2. Drop the three leftover custom field keys from any document type.
WITH stripped AS (
    UPDATE registry.document_types
       SET custom_field_schema = COALESCE(
               (SELECT jsonb_agg(elem)
                  FROM jsonb_array_elements(custom_field_schema) elem
                 WHERE elem->>'key' NOT IN
                       ('aktivnost','data_dokumenta','yurisdikciya')),
               '[]'::jsonb
           ),
           updated_at = now()
     WHERE custom_field_schema @? '$[*] ? (@.key == "aktivnost"
                                        || @.key == "data_dokumenta"
                                        || @.key == "yurisdikciya")'
    RETURNING code
)
SELECT count(*) AS types_with_keys_stripped FROM stripped;

-- 3. Defensively strip the same keys from any documents that may have
--    accumulated values (no-op if values are already empty).
WITH cleaned AS (
    UPDATE registry.documents
       SET custom_field_values = custom_field_values
                                  - 'aktivnost'
                                  - 'data_dokumenta'
                                  - 'yurisdikciya',
           updated_at = now()
     WHERE custom_field_values ?| ARRAY['aktivnost','data_dokumenta','yurisdikciya']
    RETURNING id
)
SELECT count(*) AS docs_with_values_stripped FROM cleaned;

COMMIT;

-- Verification.
SELECT 'documents (active)' AS what,
       count(*) FILTER (WHERE deleted_at IS NULL) AS live,
       count(*) FILTER (WHERE deleted_at IS NOT NULL) AS soft_deleted
  FROM registry.documents;

SELECT code, jsonb_pretty(custom_field_schema) AS schema
  FROM registry.document_types
 ORDER BY code;
