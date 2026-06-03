// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for ColumnFilterButton + useOpenColumnPopover + ColumnFilterHeaderContent.
 *
 * Covers:
 * - Button renders with correct aria attributes
 * - Active state (activeCount > 0) adds filled icon + badge
 * - Click calls onToggle and stops propagation
 * - null/undefined filterType renders nothing
 * - useOpenColumnPopover singleton: toggle, close, isOpen
 * - ColumnFilterHeaderContent renders label + button
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ColumnFilterButton,
  ColumnFilterHeaderContent,
  useOpenColumnPopover,
} from "../filters/ColumnFilterButton";
import { renderHook, act } from "@testing-library/react";

afterEach(() => cleanup());

// ── ColumnFilterButton ─────────────────────────────────────────────────────────

describe("ColumnFilterButton", () => {
  it("returns null when filterType is null", () => {
    const { container } = render(
      <ColumnFilterButton
        columnId="notes"
        columnLabel="Примечания"
        filterType={null}
        activeCount={0}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders button with aria-haspopup=dialog when filterType is set", () => {
    render(
      <ColumnFilterButton
        columnId="number"
        columnLabel="Номер"
        filterType="text"
        activeCount={0}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const btn = screen.getByRole("button");
    expect(btn).toBeTruthy();
    expect(btn.getAttribute("aria-haspopup")).toBe("dialog");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
  });

  it("aria-label mentions column name when inactive", () => {
    render(
      <ColumnFilterButton
        columnId="number"
        columnLabel="Номер"
        filterType="text"
        activeCount={0}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("aria-label")).toContain("Номер");
    expect(btn.getAttribute("aria-label")).toContain("не активен");
  });

  it("aria-label mentions active count when filter is active", () => {
    render(
      <ColumnFilterButton
        columnId="number"
        columnLabel="Номер"
        filterType="text"
        activeCount={2}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("aria-label")).toContain("активен");
    expect(btn.getAttribute("aria-label")).toContain("2");
  });

  it("shows numeric badge when activeCount > 0", () => {
    render(
      <ColumnFilterButton
        columnId="number"
        columnLabel="Номер"
        filterType="text"
        activeCount={3}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // Badge is aria-hidden span with the count
    const badge = screen.getByText("3");
    expect(badge.getAttribute("aria-hidden")).toBe("true");
  });

  it("does not render badge when activeCount is 0", () => {
    render(
      <ColumnFilterButton
        columnId="number"
        columnLabel="Номер"
        filterType="text"
        activeCount={0}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // No number text in the DOM
    expect(screen.queryByText("0")).toBeNull();
  });

  it("calls onToggle when button is clicked", () => {
    const onToggle = vi.fn();
    render(
      <ColumnFilterButton
        columnId="number"
        columnLabel="Номер"
        filterType="text"
        activeCount={0}
        isOpen={false}
        onToggle={onToggle}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("aria-expanded=true when isOpen=true", () => {
    render(
      <ColumnFilterButton
        columnId="number"
        columnLabel="Номер"
        filterType="text"
        activeCount={0}
        isOpen={true}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("true");
  });

  it("has data-testid attribute", () => {
    render(
      <ColumnFilterButton
        columnId="expiry_date"
        columnLabel="Действителен до"
        filterType="date-system"
        activeCount={0}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(
      document.querySelector('[data-testid="column-filter-btn-expiry_date"]'),
    ).toBeTruthy();
  });
});

// ── useOpenColumnPopover ────────────────────────────────────────────────────────

describe("useOpenColumnPopover", () => {
  it("initially has no column open", () => {
    const { result } = renderHook(() => useOpenColumnPopover());
    expect(result.current.columnId).toBeNull();
    expect(result.current.isOpen("col-a")).toBe(false);
  });

  it("openColumn sets the open id", () => {
    const { result } = renderHook(() => useOpenColumnPopover());
    act(() => result.current.openColumn("col-a"));
    expect(result.current.columnId).toBe("col-a");
    expect(result.current.isOpen("col-a")).toBe(true);
    expect(result.current.isOpen("col-b")).toBe(false);
  });

  it("closeColumn clears the open id", () => {
    const { result } = renderHook(() => useOpenColumnPopover());
    act(() => result.current.openColumn("col-a"));
    act(() => result.current.closeColumn());
    expect(result.current.columnId).toBeNull();
    expect(result.current.isOpen("col-a")).toBe(false);
  });

  it("toggleColumn opens when closed", () => {
    const { result } = renderHook(() => useOpenColumnPopover());
    act(() => result.current.toggleColumn("col-a"));
    expect(result.current.isOpen("col-a")).toBe(true);
  });

  it("toggleColumn closes when already open", () => {
    const { result } = renderHook(() => useOpenColumnPopover());
    act(() => result.current.openColumn("col-a"));
    act(() => result.current.toggleColumn("col-a"));
    expect(result.current.isOpen("col-a")).toBe(false);
  });

  it("opening another column closes the previous one (singleton)", () => {
    const { result } = renderHook(() => useOpenColumnPopover());
    act(() => result.current.openColumn("col-a"));
    act(() => result.current.openColumn("col-b"));
    expect(result.current.isOpen("col-a")).toBe(false);
    expect(result.current.isOpen("col-b")).toBe(true);
  });
});

// ── ColumnFilterHeaderContent ──────────────────────────────────────────────────

describe("ColumnFilterHeaderContent", () => {
  it("renders the label text", () => {
    render(
      <ColumnFilterHeaderContent
        columnId="number"
        label="Номер документа"
        filterType="text"
        activeCount={0}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Номер документа")).toBeTruthy();
  });

  it("renders the filter button alongside the label", () => {
    render(
      <ColumnFilterHeaderContent
        columnId="number"
        label="Номер документа"
        filterType="text"
        activeCount={0}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // Should have a button (the filter icon button)
    expect(screen.getByRole("button")).toBeTruthy();
  });

  it("uses columnLabel prop for aria-label if provided", () => {
    render(
      <ColumnFilterHeaderContent
        columnId="number"
        label={<span>Номер</span>}
        columnLabel="Номер документа"
        filterType="text"
        activeCount={0}
        isOpen={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("aria-label")).toContain("Номер документа");
  });
});
