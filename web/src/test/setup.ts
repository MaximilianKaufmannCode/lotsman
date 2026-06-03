// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import "@testing-library/jest-dom";
import { expect } from "vitest";
import { configureAxe } from "vitest-axe";
// vitest-axe's extend-expect.js is empty in v0.1.0; import the matcher directly from dist
import { toHaveNoViolations } from "vitest-axe/dist/matchers.js";

// Configure axe to skip color-contrast (requires canvas API not available in jsdom).
// Color-contrast is verified in Playwright e2e tests against a real browser.
// globalOptions maps to axe-core's configure() Spec; rules is RunOptions passed per-run.
configureAxe({
  globalOptions: {
    rules: [{ id: "color-contrast", enabled: false }],
  },
});

// Register vitest-axe matcher (extend-expect.js in vitest-axe@0.1.0 is empty;
// we register the matcher manually here)
expect.extend({ toHaveNoViolations });

// Mock window.matchMedia for jsdom — not implemented by default
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Suppress known harmless console errors in test environment
const originalError = console.error.bind(console);
console.error = (...args: unknown[]) => {
  const message = typeof args[0] === "string" ? args[0] : "";
  if (message.includes("Warning: An update to") && message.includes("inside a test")) {
    return;
  }
  originalError(...args);
};
