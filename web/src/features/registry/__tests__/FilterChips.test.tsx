// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for FilterChips — chip building logic and interaction.
 *
 * Run:
 *   pnpm vitest run src/features/registry/__tests__/FilterChips.test.tsx
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildChips, FilterChips } from "../filters/FilterChips";
import type { RegistrySearch } from "../hooks/useUrlState";
import { registrySearchSchema } from "../hooks/useUrlState";

afterEach(() => cleanup());

const base: RegistrySearch = registrySearchSchema.parse({});

// ---------------------------------------------------------------------------
// buildChips unit tests
// ---------------------------------------------------------------------------

describe("buildChips", () => {
  it("returns empty array for empty search", () => {
    expect(buildChips(base)).toHaveLength(0);
  });

  it("builds chip for type_codes", () => {
    const chips = buildChips({ ...base, type_codes: ["contract"] });
    expect(chips).toHaveLength(1);
    expect(chips[0]?.filterKey).toBe("type_codes");
    expect(chips[0]?.label).toBe("Тип");
    expect(chips[0]?.value).toBe("contract");
  });

  it("builds chip for multiple type_codes", () => {
    const chips = buildChips({ ...base, type_codes: ["contract", "license", "audit"] });
    expect(chips).toHaveLength(1);
    const chip = chips.at(0);
    expect(chip?.value).toContain("contract");
    expect(chip?.value).toContain("license");
  });

  it("falls back to legacy type_code when type_codes absent", () => {
    const chips = buildChips({ ...base, type_code: "contract" });
    expect(chips).toHaveLength(1);
    expect(chips[0]?.filterKey).toBe("type_code");
  });

  it("builds chip for responsible=me", () => {
    const chips = buildChips({ ...base, responsible: "me" });
    expect(chips).toHaveLength(1);
    expect(chips[0]?.value).toBe("Я");
  });

  it("builds chip for responsible=unassigned", () => {
    const chips = buildChips({ ...base, responsible: "unassigned" });
    expect(chips).toHaveLength(1);
    expect(chips[0]?.value).toBe("Не назначен");
  });

  it("builds chip for expiry_perpetual", () => {
    const chips = buildChips({ ...base, expiry_perpetual: true });
    expect(chips).toHaveLength(1);
    expect(chips[0]?.value).toBe("Бессрочные");
  });

  it("builds combined expiry chip for range", () => {
    const chips = buildChips({
      ...base,
      expiry_from: "2026-01-01",
      expiry_to: "2026-12-31",
    });
    expect(chips).toHaveLength(1);
    expect(chips[0]?.filterKey).toBe("expiry_from");
    expect(chips[0]?.value).toContain("01.01.2026");
    expect(chips[0]?.value).toContain("31.12.2026");
  });

  it("builds chip for jurisdiction CSV", () => {
    const chips = buildChips({ ...base, jurisdiction: ["RU", "KZ"] });
    expect(chips).toHaveLength(1);
    expect(chips[0]?.label).toBe("Юрисдикция");
    expect(chips[0]?.value).toContain("RU");
    expect(chips[0]?.value).toContain("KZ");
  });

  it("does not build chip for default doc_status=[active]", () => {
    const chips = buildChips({ ...base, doc_status: ["active"] });
    expect(chips).toHaveLength(0);
  });

  it("builds chip for non-default doc_status=[active,archived]", () => {
    const chips = buildChips({ ...base, doc_status: ["active", "archived"] });
    expect(chips).toHaveLength(1);
    expect(chips[0]?.label).toBe("Статус документа");
  });

  it("builds multiple chips for multiple conditions", () => {
    const chips = buildChips({
      ...base,
      type_codes: ["contract"],
      responsible: "me",
      expiry_to: "2026-12-31",
    });
    // type_codes + responsible + expiry (from/to as one chip)
    expect(chips).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// FilterChips component rendering tests
// ---------------------------------------------------------------------------

describe("FilterChips component", () => {
  it("renders nothing when no active filters", () => {
    const { container } = render(
      <FilterChips search={base} onRemove={vi.fn()} onClearAll={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders chip with label and value for type_codes", () => {
    render(
      <FilterChips
        search={{ ...base, type_codes: ["contract"] }}
        onRemove={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(screen.getByText(/Тип/)).toBeTruthy();
    expect(screen.getByText("contract")).toBeTruthy();
  });

  it("renders 'Очистить всё' button when chips present", () => {
    render(
      <FilterChips
        search={{ ...base, type_codes: ["contract"] }}
        onRemove={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(screen.getByText("Очистить всё")).toBeTruthy();
  });

  it("calls onClearAll when Очистить всё is clicked", () => {
    const onClearAll = vi.fn();
    render(
      <FilterChips
        search={{ ...base, type_codes: ["contract"] }}
        onRemove={vi.fn()}
        onClearAll={onClearAll}
      />,
    );
    fireEvent.click(screen.getByText("Очистить всё"));
    expect(onClearAll).toHaveBeenCalledTimes(1);
  });

  it("calls onRemove with correct key when ✕ is clicked", () => {
    const onRemove = vi.fn();
    render(
      <FilterChips
        search={{ ...base, type_codes: ["contract"] }}
        onRemove={onRemove}
        onClearAll={vi.fn()}
      />,
    );
    // Find the remove button by aria-label
    const removeBtn = screen.getByLabelText(/Удалить условие Тип/);
    fireEvent.click(removeBtn);
    expect(onRemove).toHaveBeenCalledWith("type_codes");
  });

  it("calls onChipClick when chip text is clicked", () => {
    const onChipClick = vi.fn();
    render(
      <FilterChips
        search={{ ...base, responsible: "me" }}
        onRemove={vi.fn()}
        onClearAll={vi.fn()}
        onChipClick={onChipClick}
      />,
    );
    // Click on the chip text (not the ✕ button)
    const chipTexts = screen.getAllByRole("button");
    const textBtn = chipTexts.find(
      (b) => !b.getAttribute("aria-label")?.includes("Удалить") && b.textContent?.includes("Я"),
    );
    if (textBtn) fireEvent.click(textBtn);
    expect(onChipClick).toHaveBeenCalledWith("responsible");
  });

  it("shows overflow button when more than 5 chips", () => {
    const manyFilters: RegistrySearch = {
      ...base,
      type_codes: ["contract"],
      responsible: "me",
      expiry_from: "2026-01-01",
      jurisdiction: ["RU", "KZ"],
      inn: "123456",
      doc_status: ["active", "archived"],
      number: "ДГ",
    };
    render(<FilterChips search={manyFilters} onRemove={vi.fn()} onClearAll={vi.fn()} />);
    // There should be an overflow button
    const overflowBtn = screen.queryByText(/ещё/i);
    expect(overflowBtn).toBeTruthy();
  });

  it("has correct aria-label on region", () => {
    render(
      <FilterChips
        search={{ ...base, type_codes: ["contract"] }}
        onRemove={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    expect(screen.getByRole("region", { name: "Активные фильтры" })).toBeTruthy();
  });

  it("chip remove button has accessible aria-label", () => {
    render(
      <FilterChips
        search={{ ...base, type_codes: ["contract"] }}
        onRemove={vi.fn()}
        onClearAll={vi.fn()}
      />,
    );
    const btn = screen.getByLabelText(/Удалить условие Тип: contract/);
    expect(btn).toBeTruthy();
  });
});
