// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Auth feature constants.
 *
 * NOTE: ACCESS_TOKEN_TTL_MS is a **default fallback** used when the JWT `exp`
 * claim is not yet available (e.g., immediately after page load before the first
 * token parse). The refresh scheduler always derives the actual TTL from the
 * JWT `exp` claim directly, so changes to ACCESS_TOKEN_TTL_SECONDS in auth-service
 * config are automatically honoured without touching this file.
 *
 * See ADR-0003 §7 (access JWT TTL) and §13 (amendment 2026-05-12 — configurable TTL).
 */

/** Refresh the access token this many ms before it expires (1 min before default TTL). */
export const REFRESH_BEFORE_EXPIRY_MS = 60_000;

/**
 * Default access token TTL in ms (15 minutes per ADR-0003 §7).
 * Used only as a fallback; actual TTL is read from the JWT `exp` claim.
 */
export const ACCESS_TOKEN_TTL_MS = 15 * 60 * 1000;
