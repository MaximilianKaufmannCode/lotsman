// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for useAttachments hook constants and client-side validation (US-9).
 *
 * Run:
 *   pnpm vitest run src/features/registry/__tests__/useAttachments.test.tsx
 */

import { describe, expect, it } from "vitest";
import { ALLOWED_MIME_TYPES, MAX_ATTACHMENT_SIZE_BYTES } from "../hooks/useAttachments";

// ---------------------------------------------------------------------------
// Constants — US-9 Q1 + Q7
// ---------------------------------------------------------------------------

describe("Attachment constants", () => {
  it("MAX_ATTACHMENT_SIZE_BYTES is exactly 25 MiB (Q1)", () => {
    expect(MAX_ATTACHMENT_SIZE_BYTES).toBe(25 * 1024 * 1024);
  });

  it("MAX_ATTACHMENT_SIZE_BYTES is less than 26 MiB (boundary: 26 MiB is rejected)", () => {
    const mib26 = 26 * 1024 * 1024;
    expect(mib26).toBeGreaterThan(MAX_ATTACHMENT_SIZE_BYTES);
  });

  it("ALLOWED_MIME_TYPES includes application/pdf (Q7)", () => {
    expect(ALLOWED_MIME_TYPES).toContain("application/pdf");
  });

  it("ALLOWED_MIME_TYPES includes image/jpeg (Q7)", () => {
    expect(ALLOWED_MIME_TYPES).toContain("image/jpeg");
  });

  it("ALLOWED_MIME_TYPES includes image/png (Q7)", () => {
    expect(ALLOWED_MIME_TYPES).toContain("image/png");
  });

  it("ALLOWED_MIME_TYPES includes image/tiff (Q7)", () => {
    expect(ALLOWED_MIME_TYPES).toContain("image/tiff");
  });

  it("ALLOWED_MIME_TYPES includes docx MIME (Q7)", () => {
    expect(ALLOWED_MIME_TYPES).toContain(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    );
  });

  it("ALLOWED_MIME_TYPES includes xlsx MIME (Q7)", () => {
    expect(ALLOWED_MIME_TYPES).toContain(
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    );
  });

  it("ALLOWED_MIME_TYPES does NOT include application/x-dosexec (executables rejected)", () => {
    expect(ALLOWED_MIME_TYPES).not.toContain("application/x-dosexec");
  });

  it("ALLOWED_MIME_TYPES does NOT include text/html (HTML injection vector rejected)", () => {
    expect(ALLOWED_MIME_TYPES).not.toContain("text/html");
  });

  it("ALLOWED_MIME_TYPES has exactly 6 entries (Q7 allowlist is exhaustive)", () => {
    expect(ALLOWED_MIME_TYPES).toHaveLength(6);
  });
});

// ---------------------------------------------------------------------------
// Upload file size gate — client-side (US-9 Q1 edge)
// ---------------------------------------------------------------------------

describe("client-side file size gate", () => {
  it("a 25 MiB file is at the exact limit (allowed)", () => {
    const fileSize = 25 * 1024 * 1024;
    expect(fileSize).toBe(MAX_ATTACHMENT_SIZE_BYTES);
    // At the boundary: size === MAX is allowed (the check in useAttachments is >)
    expect(fileSize > MAX_ATTACHMENT_SIZE_BYTES).toBe(false);
  });

  it("a 25 MiB + 1 byte file exceeds the limit (rejected)", () => {
    const fileSize = 25 * 1024 * 1024 + 1;
    expect(fileSize > MAX_ATTACHMENT_SIZE_BYTES).toBe(true);
  });

  it("a 26 MiB file is rejected (US-9 edge)", () => {
    const fileSize = 26 * 1024 * 1024;
    expect(fileSize > MAX_ATTACHMENT_SIZE_BYTES).toBe(true);
  });
});
