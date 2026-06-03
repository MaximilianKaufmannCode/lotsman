// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * computeStatus — pure function mirroring backend's compute_status logic.
 * Used as a fallback when the API response doesn't include a pre-computed status,
 * and as the single source of truth for tests.
 *
 * Rules (from the requirements §8 Glossary, US-21):
 *  - "В архиве"   → deleted_at IS NOT NULL (highest priority, overrides all)
 *  - "ОК"         → expiry_date IS NULL OR expiry_date > today + 30 calendar days
 *  - "Скоро"      → 0 ≤ expiry_date - today ≤ 30 calendar days (in-day = "Скоро")
 *  - "Просрочено" → expiry_date < today (strictly before today)
 *
 * Boundary: expiry_date === today → "Скоро" (in-day, NOT "Просрочено")
 * Calendar days (Q4 decision): no weekend/holiday shifts.
 */

import type { ComputedStatus } from "./types";

const PRE_NOTICE_DAYS = 30;

/**
 * @param expiryDate  ISO-8601 date string (YYYY-MM-DD) or null
 * @param deletedAt   ISO-8601 timestamp string or null
 * @param today       Date to evaluate against; defaults to new Date() for production
 */
export function computeStatus(
  expiryDate: string | null,
  deletedAt: string | null,
  today: Date = new Date(),
): ComputedStatus {
  // Archived takes absolute priority
  if (deletedAt !== null) return "archived";

  // No expiry = indefinitely valid
  if (expiryDate === null) return "ok";

  // Compare calendar dates only (strip time component to avoid timezone drift)
  const todayMs = dateOnlyMs(today);
  const expiryMs = dateOnlyMs(new Date(expiryDate));

  const diffDays = (expiryMs - todayMs) / (1000 * 60 * 60 * 24);

  if (diffDays < 0) return "overdue";
  if (diffDays <= PRE_NOTICE_DAYS) return "soon";
  return "ok";
}

/** Returns midnight UTC for a given date to allow pure calendar-day comparison. */
function dateOnlyMs(d: Date): number {
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}
