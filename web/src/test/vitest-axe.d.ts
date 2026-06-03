/**
 * Extend vitest's re-exported Assertion with toHaveNoViolations from vitest-axe.
 *
 * vitest-axe@0.1.0 was written for an older Vi namespace that no longer exists
 * in vitest v3. We re-declare the matcher here against the "vitest" module
 * which re-exports Assertion from @vitest/expect.
 *
 * Consumers (*.test.tsx) import { expect } from "vitest" and get
 * Assertion<T> back — this augmentation adds toHaveNoViolations to it.
 */
declare module "vitest" {
  // Vitest v3 re-exports Assertion from @vitest/expect.
  interface Assertion<T = unknown> {
    toHaveNoViolations(): void;
  }
  interface AsymmetricMatchersContaining {
    toHaveNoViolations(): void;
  }
}

export {};
