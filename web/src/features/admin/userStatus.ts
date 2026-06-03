// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Single source of truth for a user's displayed status.
 *
 * Used by the users list badge, the detail-drawer header, AND the list filter so
 * they can never drift (the list badge previously lacked a "pending" branch, so
 * invited-but-not-enrolled users showed «Активен» in the list yet «Ожидает
 * активации» in the card).
 *
 * Priority: system service account → deactivated → locked → pending → active.
 */

const SYSTEM_EMAIL_SUFFIX = "@system.lotsman";

export type UserStatusKey = "system" | "deactivated" | "locked" | "pending" | "active";

export interface UserStatusInfo {
  key: UserStatusKey;
  label: string;
  /** Tailwind classes for the compact list badge (bg + text). */
  badgeClass: string;
  /** Colored status dot (drawer header). */
  dotClass: string;
  /** Text color (drawer header). */
  textClass: string;
}

interface StatusInput {
  email: string;
  is_active: boolean;
  is_locked: boolean;
  totp_enrolled: boolean;
}

export function computeUserStatus(u: StatusInput): UserStatusInfo {
  if (u.email.endsWith(SYSTEM_EMAIL_SUFFIX)) {
    return {
      key: "system",
      label: "Системная",
      badgeClass: "bg-muted text-muted-foreground",
      dotClass: "bg-gray-400",
      textClass: "text-gray-500",
    };
  }
  if (!u.is_active) {
    return {
      key: "deactivated",
      label: "Деактивирован",
      badgeClass: "bg-muted text-muted-foreground",
      dotClass: "bg-gray-400",
      textClass: "text-gray-500",
    };
  }
  if (u.is_locked) {
    return {
      key: "locked",
      label: "Заблокирован",
      badgeClass: "bg-status-overdue/10 text-status-overdue",
      dotClass: "bg-red-500",
      textClass: "text-red-600",
    };
  }
  if (!u.totp_enrolled) {
    return {
      key: "pending",
      label: "Ожидает активации",
      badgeClass: "bg-amber-100 text-amber-800",
      dotClass: "bg-amber-500",
      textClass: "text-amber-600",
    };
  }
  return {
    key: "active",
    label: "Активен",
    badgeClass: "bg-status-ok/10 text-status-ok",
    dotClass: "bg-green-500",
    textClass: "text-green-700",
  };
}
