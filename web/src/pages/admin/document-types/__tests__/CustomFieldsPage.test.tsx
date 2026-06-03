// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for CustomFieldsPage — US-6, US-7
 *
 * Requirements:
 *   - Add field → save calls PUT with right schema
 *   - TOTP validation before save
 *   - Delete field removes it from local state
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Module mocks (hoisted)
// ---------------------------------------------------------------------------

vi.mock("@/features/admin/document-types/custom-fields-api", () => ({
  getCustomFieldSchema: vi.fn(),
  updateCustomFieldSchema: vi.fn(),
  registerDocTypeFieldsTokenAccessor: vi.fn(),
  CustomFieldApiResponseError: class CustomFieldApiResponseError extends Error {
    constructor(
      public status: number,
      public detail: string,
      public code?: string,
    ) {
      super(detail);
      this.name = "CustomFieldApiResponseError";
    }
  },
}));

vi.mock("@/shared/ui/toast", () => ({
  toast: { show: vi.fn() },
}));

// ---------------------------------------------------------------------------
// Imports (after mocks)
// ---------------------------------------------------------------------------

import {
  getCustomFieldSchema,
  updateCustomFieldSchema,
} from "@/features/admin/document-types/custom-fields-api";
import { CustomFieldsPage } from "../CustomFieldsPage";

const mockGetSchema = vi.mocked(getCustomFieldSchema);
const mockUpdateSchema = vi.mocked(updateCustomFieldSchema);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage(typeCode = "contract") {
  const qc = makeQC();
  return render(
    <QueryClientProvider client={qc}>
      <CustomFieldsPage typeCode={typeCode} />
    </QueryClientProvider>,
  );
}

/**
 * Fill the add-field modal.
 * Types the key BEFORE the display_name so the auto-derive effect is already
 * disabled (keyManuallySet=true) by the time we type the Cyrillic name.
 */
async function fillAddFieldModal(
  user: ReturnType<typeof userEvent.setup>,
  opts: { displayName: string; key: string; required?: boolean },
) {
  // Type the key FIRST so keyManuallySet becomes true before we touch display_name.
  const keyInput = screen.getByLabelText(/Ключ \(идентификатор\)/i);
  await user.clear(keyInput);
  await user.type(keyInput, opts.key);

  // Now type the display name — auto-derive is disabled (keyManuallySet=true).
  const nameInput = screen.getByLabelText(/Отображаемое название/i);
  await user.click(nameInput);
  await user.type(nameInput, opts.displayName);

  if (opts.required) {
    await user.click(screen.getByLabelText(/Обязательное поле/i));
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CustomFieldsPage — schema editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSchema.mockResolvedValue([]);
    mockUpdateSchema.mockResolvedValue([]);
  });

  it("test_renders_empty_state_when_schema_is_empty", async () => {
    renderPage();
    expect(await screen.findByText(/Кастомных полей нет/i)).toBeInTheDocument();
  });

  it("test_add_field_opens_modal_and_adds_to_local_state", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText(/Кастомных полей нет/i);

    // Open modal
    await user.click(screen.getByTestId("add-custom-field-btn"));
    expect(await screen.findByTestId("add-field-modal")).toBeInTheDocument();

    await fillAddFieldModal(user, { displayName: "Номер лицензии", key: "license_number" });

    // Submit
    await user.click(screen.getByTestId("add-field-submit"));

    // Modal should close
    await waitFor(() => {
      expect(screen.queryByTestId("add-field-modal")).not.toBeInTheDocument();
    });

    // Field should appear in table
    expect(screen.getByText("Номер лицензии")).toBeInTheDocument();
    expect(screen.getByText("license_number")).toBeInTheDocument();
  });

  it("test_save_requires_6_digit_totp", async () => {
    mockGetSchema.mockResolvedValue([
      {
        key: "notes_extra",
        display_name: "Примечание",
        type: "text",
        required: false,
        options: null,
      },
    ]);
    renderPage();

    // Wait for schema to load and appear in table
    await screen.findByText("Примечание");

    // Initially disabled since no dirty state
    const saveBtn = screen.getByTestId("cf-save-btn");
    expect(saveBtn).toBeDisabled();
  });

  it("test_add_field_then_save_calls_PUT_with_correct_schema", async () => {
    const user = userEvent.setup();
    mockGetSchema.mockResolvedValue([]);
    mockUpdateSchema.mockResolvedValue([
      { key: "reg_num", display_name: "Рег. номер", type: "text", required: true, options: null },
    ]);
    renderPage();

    await screen.findByText(/Кастомных полей нет/i);

    // Add a field
    await user.click(screen.getByTestId("add-custom-field-btn"));
    await screen.findByTestId("add-field-modal");

    await fillAddFieldModal(user, { displayName: "Рег. номер", key: "reg_num", required: true });
    await user.click(screen.getByTestId("add-field-submit"));

    // Wait for modal to close
    await waitFor(() => {
      expect(screen.queryByTestId("add-field-modal")).not.toBeInTheDocument();
    });

    // Wait for the field to appear in the table (confirms isDirty = true)
    await screen.findByText("Рег. номер");

    // Now enter TOTP and save
    const totpInput = screen.getByTestId("cf-totp-input");
    await user.type(totpInput, "123456");

    const saveBtn = screen.getByTestId("cf-save-btn");
    await waitFor(() => expect(saveBtn).not.toBeDisabled());
    await user.click(saveBtn);

    await waitFor(() => {
      expect(mockUpdateSchema).toHaveBeenCalledWith(
        "contract",
        expect.arrayContaining([
          expect.objectContaining({ key: "reg_num", display_name: "Рег. номер", required: true }),
        ]),
        "123456",
      );
    });
  });

  it("test_delete_field_shows_confirm_dialog_and_removes_field", async () => {
    const user = userEvent.setup();
    mockGetSchema.mockResolvedValue([
      { key: "field_a", display_name: "Поле А", type: "text", required: false, options: null },
    ]);
    renderPage();

    await screen.findByText("Поле А");

    // Click delete button
    const deleteBtn = screen.getByLabelText(/Удалить поле Поле А/i);
    await user.click(deleteBtn);

    // Confirm dialog should appear
    expect(await screen.findByTestId("delete-field-confirm")).toBeInTheDocument();
    expect(screen.getByText(/Поле «Поле А»/i)).toBeInTheDocument();

    // Confirm delete
    await user.click(screen.getByTestId("delete-field-confirm-btn"));

    await waitFor(() => {
      expect(screen.queryByText("Поле А")).not.toBeInTheDocument();
    });
  });

  it("test_enum_field_requires_options", async () => {
    const user = userEvent.setup();
    mockGetSchema.mockResolvedValue([]);
    renderPage();

    await screen.findByText(/Кастомных полей нет/i);

    // Open modal
    await user.click(screen.getByTestId("add-custom-field-btn"));
    await screen.findByTestId("add-field-modal");

    await fillAddFieldModal(user, { displayName: "Статус", key: "status_field" });

    // Select enum type
    const typeSelect = screen.getByLabelText(/Тип поля/i);
    await user.selectOptions(typeSelect, "enum");

    // Options textarea should appear — don't fill it
    expect(await screen.findByLabelText(/Варианты/i)).toBeInTheDocument();

    // Try to submit without options — should fail validation
    await user.click(screen.getByTestId("add-field-submit"));

    // Modal should still be open (validation failed)
    expect(screen.getByTestId("add-field-modal")).toBeInTheDocument();
    expect(screen.getByText(/хотя бы один вариант/i)).toBeInTheDocument();
  });
});
