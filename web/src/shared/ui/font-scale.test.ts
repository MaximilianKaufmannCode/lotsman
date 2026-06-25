// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clampScale,
  DEFAULT_SCALE,
  deriveRowHeight,
  MAX_SCALE,
  MIN_SCALE,
  optionForPercent,
  SCALE_OPTIONS,
  STORAGE_KEY,
  setScale,
} from "./font-scale";

// jsdom does not expose localStorage under the default (opaque) origin, so the
// module guards every access with try/catch. Provide an in-memory stub here so
// the persistence assertions have a real Storage to read back.
class MemoryStorage {
  private store = new Map<string, string>();
  get length(): number {
    return this.store.size;
  }
  clear(): void {
    this.store.clear();
  }
  getItem(key: string): string | null {
    return this.store.has(key) ? (this.store.get(key) as string) : null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }
}

beforeEach(() => {
  vi.stubGlobal("localStorage", new MemoryStorage());
  document.documentElement.style.removeProperty("--app-font-scale");
});

afterAll(() => {
  vi.unstubAllGlobals();
});

describe("clampScale", () => {
  it("passes through valid in-range percents (rounded to int)", () => {
    expect(clampScale(90)).toBe(90);
    expect(clampScale(100)).toBe(100);
    expect(clampScale(115)).toBe(115);
    expect(clampScale("130")).toBe(130);
    expect(clampScale(112.5)).toBe(113);
  });

  it("clamps out-of-range values to the bounds", () => {
    expect(clampScale(5)).toBe(MIN_SCALE);
    expect(clampScale(0)).toBe(MIN_SCALE);
    expect(clampScale(9999)).toBe(MAX_SCALE);
  });

  it("falls back to default for garbage / non-numeric input", () => {
    expect(clampScale(Number.NaN)).toBe(DEFAULT_SCALE);
    expect(clampScale("abc")).toBe(DEFAULT_SCALE);
    expect(clampScale(null)).toBe(DEFAULT_SCALE);
    expect(clampScale(undefined)).toBe(DEFAULT_SCALE);
  });
});

describe("SCALE_OPTIONS / optionForPercent", () => {
  it("offers exactly 4 presets with normal == default", () => {
    expect(SCALE_OPTIONS).toHaveLength(4);
    expect(optionForPercent(DEFAULT_SCALE)?.key).toBe("normal");
    expect(optionForPercent(100)?.percent).toBe(100);
  });

  it("returns the matching preset, or null when off-grid", () => {
    expect(optionForPercent(90)?.key).toBe("compact");
    expect(optionForPercent(115)?.key).toBe("large");
    expect(optionForPercent(130)?.key).toBe("xlarge");
    expect(optionForPercent(123)).toBeNull();
  });
});

describe("deriveRowHeight", () => {
  it("returns the base height at scale 100 (byte-identical to before)", () => {
    expect(deriveRowHeight(48, 100)).toBe(48);
  });

  it("scales proportionally with the font preference", () => {
    expect(deriveRowHeight(48, 90)).toBe(43); // round(43.2)
    expect(deriveRowHeight(48, 115)).toBe(55); // round(55.2)
    expect(deriveRowHeight(48, 130)).toBe(62); // round(62.4)
  });
});

describe("setScale", () => {
  it("applies the CSS variable (percent/100) and caches the percent", () => {
    const applied = setScale(115);
    expect(applied).toBe(115);
    expect(document.documentElement.style.getPropertyValue("--app-font-scale")).toBe("1.15");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("115");
  });

  it("clamps an out-of-range request before applying", () => {
    const applied = setScale(9999);
    expect(applied).toBe(MAX_SCALE);
    expect(document.documentElement.style.getPropertyValue("--app-font-scale")).toBe(
      String(MAX_SCALE / 100),
    );
    expect(localStorage.getItem(STORAGE_KEY)).toBe(String(MAX_SCALE));
  });

  it("default scale restores the baseline multiplier of 1", () => {
    setScale(DEFAULT_SCALE);
    expect(document.documentElement.style.getPropertyValue("--app-font-scale")).toBe("1");
  });
});
