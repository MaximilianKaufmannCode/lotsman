// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { axe } from "vitest-axe";
import { type DocumentStatus, StatusBadge } from "./status-badge";

const statuses: DocumentStatus[] = ["ok", "soon", "overdue", "archived"];

describe("StatusBadge", () => {
  it.each(statuses)("renders icon AND text label for status=%s (not color-only)", (status) => {
    const { container } = render(<StatusBadge status={status} />);

    // Must have a visible text label
    const badge = container.querySelector("[data-status]");
    expect(badge).not.toBeNull();
    const text = badge?.textContent?.trim();
    expect(text).toBeTruthy();
    expect(text?.length).toBeGreaterThan(0);

    // Must have an SVG icon (aria-hidden sibling to text)
    const svg = badge?.querySelector("svg[aria-hidden]");
    expect(svg).not.toBeNull();
  });

  it("has correct data-status attribute for each variant", () => {
    for (const status of statuses) {
      const { container } = render(<StatusBadge status={status} />);
      const badge = container.querySelector(`[data-status="${status}"]`);
      expect(badge).not.toBeNull();
    }
  });

  it("is accessible (axe)", async () => {
    const { container } = render(
      <div>
        {statuses.map((s) => (
          <StatusBadge key={s} status={s} />
        ))}
      </div>,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  }, 15_000);
});
