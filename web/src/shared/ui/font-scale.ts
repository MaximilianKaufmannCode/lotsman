// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Per-user web-interface font-size preference.
 *
 * Mirrors the theme-toggle.tsx pattern (module-load apply + localStorage), but
 * exposes a tiny `useSyncExternalStore` store so any component (the profile
 * control AND the virtualized registry table) re-renders when the scale changes
 * — without a wrapping React context Provider.
 *
 * Mechanism: a single UNITLESS multiplier written to the CSS variable
 * `--app-font-scale` on <html>. globals.css does
 *   html { font-size: calc(16px * var(--app-font-scale, 1)); }
 * so scaling the root rescales every rem-based Tailwind token together.
 *
 * Storage contract:
 *  - The value is a PERCENT of the base (100 == default / today's exact look).
 *  - Persisted in localStorage key "lotsman-font-scale" as the integer string.
 *  - For authenticated users the SERVER (auth.users.ui_font_scale) is the
 *    system of record; localStorage is a write-through cache used only for the
 *    instant, flash-free apply at module load. ProfilePage reconciles the two.
 *  - The CSS variable holds percent/100 (e.g. "1.15").
 */

import * as React from "react";

export const STORAGE_KEY = "lotsman-font-scale";

/** Default percent — 100 reproduces the previous fixed 14px look exactly. */
export const DEFAULT_SCALE = 100;
/** Hard bounds — mirror the auth-service DB CHECK and API clamp (80..150). */
export const MIN_SCALE = 80;
export const MAX_SCALE = 150;

export type FontScaleKey = "compact" | "normal" | "large" | "xlarge";

export interface FontScaleOption {
  key: FontScaleKey;
  /** Percent of base; stored value + server value. */
  percent: number;
}

/**
 * The 4 presets offered in the UI. Integer percents (no float drift end-to-end).
 * `normal` MUST be exactly DEFAULT_SCALE so the default render is unchanged.
 */
export const SCALE_OPTIONS: readonly FontScaleOption[] = [
  { key: "compact", percent: 90 },
  { key: "normal", percent: 100 },
  { key: "large", percent: 115 },
  { key: "xlarge", percent: 130 },
] as const;

/** Clamp an arbitrary value to a valid integer percent, falling back to default. */
export function clampScale(value: unknown): number {
  const n = typeof value === "number" ? value : Number.parseInt(String(value), 10);
  if (!Number.isFinite(n)) return DEFAULT_SCALE;
  const rounded = Math.round(n);
  if (rounded < MIN_SCALE) return MIN_SCALE;
  if (rounded > MAX_SCALE) return MAX_SCALE;
  return rounded;
}

/** Find the preset matching a percent, or null if the value is off-grid. */
export function optionForPercent(percent: number): FontScaleOption | null {
  return SCALE_OPTIONS.find((o) => o.percent === percent) ?? null;
}

/**
 * Virtualizer row height for a given scale. The registry locks each row to a
 * fixed pixel height (estimateSize + inline <tr> height); without scaling it,
 * larger fonts clip/mis-center the cell content. Keeping it proportional
 * preserves headroom. basePx default 48 == the historical ROW_HEIGHT.
 */
export function deriveRowHeight(basePx: number, percent: number): number {
  return Math.round((basePx * percent) / 100);
}

function readStored(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return DEFAULT_SCALE;
    return clampScale(raw);
  } catch {
    // localStorage may be unavailable (private browsing, sandboxed)
    return DEFAULT_SCALE;
  }
}

/** Write the CSS variable on <html>. Pure DOM, no persistence. */
function applyToDom(percent: number): void {
  if (typeof document === "undefined") return;
  document.documentElement.style.setProperty("--app-font-scale", String(percent / 100));
}

function persist(percent: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(percent));
  } catch {
    // ignore — the DOM value still applies for this session
  }
}

// ── External store (useSyncExternalStore) ─────────────────────────────────────

let currentScale = DEFAULT_SCALE;
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function getSnapshot(): number {
  return currentScale;
}

function getServerSnapshot(): number {
  // SSR / first server render (and jsdom module init) — assume default.
  return DEFAULT_SCALE;
}

/**
 * Set the scale from a USER action: clamp, apply to the DOM, persist to
 * localStorage, and notify subscribers. Returns the clamped value actually
 * applied. Idempotent — safe under React StrictMode double-invoke.
 */
export function setScale(percent: number): number {
  const next = clampScale(percent);
  currentScale = next;
  applyToDom(next);
  persist(next);
  emit();
  return next;
}

/**
 * Apply a value coming from the SERVER (reconciliation after GET /me). Same as
 * setScale (the server is the system of record, so we cache + apply it), but
 * named distinctly at call sites to make intent clear: this must NOT trigger a
 * PATCH back to the server.
 */
export const applyServerScale = setScale;

/** React hook: current font-scale percent, re-rendering on change. */
export function useFontScale(): number {
  return React.useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

// ── Anti-FOUC module-load init ────────────────────────────────────────────────
//
// Runs when the module is first imported (early in the main.tsx → providers
// chain), BEFORE React paints, so the saved size is applied without a flash —
// exactly like theme-toggle.tsx applies the theme attribute at load.
if (typeof window !== "undefined") {
  currentScale = readStored();
  applyToDom(currentScale);
}
