// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for per-column filter popovers: TextFilterPopover, DateFilterPopover.
 *
 * Mocks:
 * - useDistinctValues → static data for TextFilterPopover
 * - date-fns → deterministic output
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks ──────────────────────────────────────────────────────────────────────

vi.mock("@/features/registry/hooks/useDistinctValues", () => ({
  useDistinctValues: vi.fn(),
}));

vi.mock("date-fns", async (importOriginal) => {
  const actual = await importOriginal<typeof import("date-fns")>();
  return {
    ...actual,
    format: (d: Date, fmt: string) => {
      // Deterministic: return ISO date portion for yyyy-MM-dd, else delegate
      if (fmt === "yyyy-MM-dd") {
        return d.toISOString().slice(0, 10);
      }
      return actual.format(d, fmt);
    },
  };
});

import { useDistinctValues } from "@/features/registry/hooks/useDistinctValues";
import { DateFilterPopover } from "../filters/popovers/DateFilterPopover";
import { TextFilterPopover } from "../filters/popovers/TextFilterPopover";

const mockUseDistinctValues = vi.mocked(useDistinctValues);

afterEach(() => cleanup());

// ── TextFilterPopover ─────────────────────────────────────────────────────────

describe("TextFilterPopover", () => {
  beforeEach(() => {
    mockUseDistinctValues.mockReturnValue({
      data: {
        field: "number",
        values: [
          { value: "ДГ-2026-001", count: 5 },
          { value: "ДГ-2026-002", count: 3 },
          { value: "ЛЦ-2026-001", count: 1 },
        ],
        total_distinct: 3,
        truncated: false,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDistinctValues>);
  });

  it("renders free-text input", () => {
    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value={undefined}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByPlaceholderText(/ДГ-2026-001/i)).toBeTruthy();
  });

  it("renders distinct values as checkboxes", () => {
    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value={undefined}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("ДГ-2026-001")).toBeTruthy();
    expect(screen.getByText("ДГ-2026-002")).toBeTruthy();
    expect(screen.getByText("ЛЦ-2026-001")).toBeTruthy();
  });

  it("initialises with free-text value from prop", () => {
    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value="ДГ-2026"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const input = screen.getByRole("textbox", { name: /Содержит/i }) as HTMLInputElement;
    expect(input.value).toBe("ДГ-2026");
  });

  it("calls onApply with free-text when Apply is clicked", () => {
    const onApply = vi.fn();
    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value={undefined}
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    const input = screen.getByRole("textbox", { name: /Содержит/i });
    fireEvent.change(input, { target: { value: "ДГ-2026" } });
    fireEvent.click(screen.getByRole("button", { name: /Применить/i }));
    expect(onApply).toHaveBeenCalledWith("ДГ-2026");
  });

  it("calls onApply with undefined when Apply is clicked with no input", () => {
    const onApply = vi.fn();
    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value={undefined}
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Применить/i }));
    expect(onApply).toHaveBeenCalledWith(undefined);
  });

  it("calls onApply with string[] when checkbox values selected", () => {
    const onApply = vi.fn();
    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value={undefined}
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    // Click the checkbox for ДГ-2026-001
    const checkbox = screen.getByLabelText(/ДГ-2026-001/);
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: /Применить/i }));
    expect(onApply).toHaveBeenCalledWith(["ДГ-2026-001"]);
  });

  it("disables free-text input when a checkbox is selected", () => {
    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value={undefined}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const checkbox = screen.getByLabelText(/ДГ-2026-001/);
    fireEvent.click(checkbox);
    const input = screen.getByRole("textbox", { name: /Содержит/i }) as HTMLInputElement;
    expect(input.disabled).toBe(true);
  });

  it("calls onClose when Esc button is pressed on popover frame", () => {
    const onClose = vi.fn();
    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value={undefined}
        onApply={vi.fn()}
        onClose={onClose}
      />,
    );
    // Esc on the dialog
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("resets draft when Сбросить is clicked", () => {
    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value="ДГ-2026"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const input = screen.getByRole("textbox", { name: /Содержит/i }) as HTMLInputElement;
    expect(input.value).toBe("ДГ-2026");
    fireEvent.click(screen.getByRole("button", { name: /Сбросить/i }));
    expect(input.value).toBe("");
  });

  it("shows loading skeletons when isLoading=true", () => {
    mockUseDistinctValues.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDistinctValues>);

    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value={undefined}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // Skeletons should be present — they render as div elements with skeleton class
    const skeletons = document.querySelectorAll(".animate-pulse");
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("shows error state with retry button when isError=true", () => {
    mockUseDistinctValues.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useDistinctValues>);

    render(
      <TextFilterPopover
        columnId="number"
        columnLabel="Номер"
        fieldName="number"
        value={undefined}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/Не удалось загрузить/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Повторить/i })).toBeTruthy();
  });
});

// ── DateFilterPopover (system-range mode) ────────────────────────────────────

describe("DateFilterPopover — system-range", () => {
  it("renders from/to date inputs", () => {
    render(
      <DateFilterPopover
        columnId="expiry_date"
        columnLabel="Действителен до"
        mode="system-range"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // Use id-based query to avoid ambiguity with "До" appearing in labels and text
    expect(document.getElementById("date-from-expiry_date")).toBeTruthy();
    expect(document.getElementById("date-to-expiry_date")).toBeTruthy();
  });

  it("initializes from/to from currentFrom/currentTo props", () => {
    render(
      <DateFilterPopover
        columnId="expiry_date"
        columnLabel="Действителен до"
        mode="system-range"
        currentFrom="2026-01-01"
        currentTo="2026-12-31"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect((document.getElementById("date-from-expiry_date") as HTMLInputElement).value).toBe(
      "2026-01-01",
    );
    expect((document.getElementById("date-to-expiry_date") as HTMLInputElement).value).toBe(
      "2026-12-31",
    );
  });

  it("shows perpetual checkbox when supportsNull=true", () => {
    render(
      <DateFilterPopover
        columnId="expiry_date"
        columnLabel="Действителен до"
        mode="system-range"
        supportsNull={true}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/Только бессрочные/i)).toBeTruthy();
  });

  it("does not show perpetual checkbox when supportsNull=false (default)", () => {
    render(
      <DateFilterPopover
        columnId="updated_at"
        columnLabel="Обновлён"
        mode="system-range"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Только бессрочные/i)).toBeNull();
  });

  it("renders quick period buttons", () => {
    render(
      <DateFilterPopover
        columnId="expiry_date"
        columnLabel="Действителен до"
        mode="system-range"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Сегодня")).toBeTruthy();
    expect(screen.getByText("7 дней")).toBeTruthy();
    expect(screen.getByText("30 дней")).toBeTruthy();
    expect(screen.getByText("Этот квартал")).toBeTruthy();
    expect(screen.getByText("Год")).toBeTruthy();
  });

  it("fills from/to when quick period button clicked", () => {
    render(
      <DateFilterPopover
        columnId="expiry_date"
        columnLabel="Действителен до"
        mode="system-range"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Сегодня"));
    const fromInput = document.getElementById("date-from-expiry_date") as HTMLInputElement;
    const toInput = document.getElementById("date-to-expiry_date") as HTMLInputElement;
    // Both should be set to today's date (format is yyyy-MM-dd)
    expect(fromInput.value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(toInput.value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(fromInput.value).toBe(toInput.value); // today = same day
  });

  it("shows validation error when from > to", async () => {
    render(
      <DateFilterPopover
        columnId="expiry_date"
        columnLabel="Действителен до"
        mode="system-range"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const fromInput = document.getElementById("date-from-expiry_date") as HTMLInputElement;
    const toInput = document.getElementById("date-to-expiry_date") as HTMLInputElement;
    fireEvent.change(fromInput, { target: { value: "2026-12-31" } });
    fireEvent.change(toInput, { target: { value: "2026-01-01" } });
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });
  });

  it("disables Apply button when range is invalid", async () => {
    render(
      <DateFilterPopover
        columnId="expiry_date"
        columnLabel="Действителен до"
        mode="system-range"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.change(
      document.getElementById("date-from-expiry_date") as HTMLInputElement,
      { target: { value: "2026-12-31" } },
    );
    fireEvent.change(
      document.getElementById("date-to-expiry_date") as HTMLInputElement,
      { target: { value: "2026-01-01" } },
    );
    await waitFor(() => {
      const applyBtn = screen.getByRole("button", { name: /Применить/i });
      expect((applyBtn as HTMLButtonElement).disabled).toBe(true);
    });
  });

  it("calls onApply with expiry_perpetual when perpetual checkbox checked", () => {
    const onApply = vi.fn();
    render(
      <DateFilterPopover
        columnId="expiry_date"
        columnLabel="Действителен до"
        mode="system-range"
        supportsNull={true}
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: /Применить/i }));
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ expiry_perpetual: true }),
    );
  });

  it("resets from/to to empty on Сбросить", () => {
    render(
      <DateFilterPopover
        columnId="expiry_date"
        columnLabel="Действителен до"
        mode="system-range"
        currentFrom="2026-01-01"
        currentTo="2026-12-31"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Сбросить/i }));
    expect((document.getElementById("date-from-expiry_date") as HTMLInputElement).value).toBe("");
    expect((document.getElementById("date-to-expiry_date") as HTMLInputElement).value).toBe("");
  });
});

// ── DateFilterPopover (custom-equality mode) ─────────────────────────────────

describe("DateFilterPopover — custom-equality", () => {
  it("renders single date input instead of range", () => {
    render(
      <DateFilterPopover
        columnId="cf_deadline"
        columnLabel="Крайний срок"
        mode="custom-equality"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    // Should have only 1 date input (not from/to)
    const dateInputs = document.querySelectorAll('input[type="date"]');
    expect(dateInputs.length).toBe(1);
  });

  it("shows V1 limitation notice", () => {
    render(
      <DateFilterPopover
        columnId="cf_deadline"
        columnLabel="Крайний срок"
        mode="custom-equality"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/Диапазон — в следующей версии/i)).toBeTruthy();
  });

  it("initialises single date from currentFrom prop", () => {
    render(
      <DateFilterPopover
        columnId="cf_deadline"
        columnLabel="Крайний срок"
        mode="custom-equality"
        currentFrom="2026-06-15"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const input = document.querySelector('input[type="date"]') as HTMLInputElement;
    expect(input.value).toBe("2026-06-15");
  });

  it("does not render quick period buttons", () => {
    render(
      <DateFilterPopover
        columnId="cf_deadline"
        columnLabel="Крайний срок"
        mode="custom-equality"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByText("Сегодня")).toBeNull();
    expect(screen.queryByText("7 дней")).toBeNull();
  });
});
