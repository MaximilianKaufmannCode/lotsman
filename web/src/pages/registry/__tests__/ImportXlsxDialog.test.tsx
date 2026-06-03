// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for ImportXlsxDialog — 2-step import wizard (US-8)
 *
 * Requirements:
 *   - Step 1: file upload → POST /admin/import/preview
 *   - Step 1 error states: 413 (too large), 422 (no headers)
 *   - Step 2: decisions + TOTP → POST /admin/import/confirm
 *   - No unknown columns → straight-through import flow
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock("@/features/admin/document-types/custom-fields-api", () => ({
  importPreview: vi.fn(),
  importConfirm: vi.fn(),
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

vi.mock("@/features/registry/hooks/useDocumentTypes", () => ({
  useDocumentTypes: vi.fn(),
}));

vi.mock("@/shared/ui/toast", () => ({
  toast: { show: vi.fn() },
}));

// ---------------------------------------------------------------------------
// Imports after mocks
// ---------------------------------------------------------------------------

import {
  CustomFieldApiResponseError,
  importConfirm,
  importPreview,
} from "@/features/admin/document-types/custom-fields-api";
import { useDocumentTypes } from "@/features/registry/hooks/useDocumentTypes";
import { toast } from "@/shared/ui/toast";
import { ImportXlsxDialog } from "../ImportXlsxDialog";

const mockPreview = vi.mocked(importPreview);
const mockConfirm = vi.mocked(importConfirm);
const mockUseDocumentTypes = vi.mocked(useDocumentTypes);
const mockToast = vi.mocked(toast.show);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderDialog(open = true, onClose = vi.fn()) {
  const qc = makeQC();
  return render(
    <QueryClientProvider client={qc}>
      <ImportXlsxDialog open={open} onClose={onClose} />
    </QueryClientProvider>,
  );
}

const PREVIEW_NO_UNKNOWN: import("@/features/admin/document-types/custom-fields-api").ImportPreviewResponse =
  {
    import_session_id: "sess-001",
    rows_total: 42,
    known_columns: [
      { header: "Компания", mapped_to: "asset_name" },
      { header: "Номер", mapped_to: "number" },
    ],
    unknown_columns: [],
  };

const PREVIEW_WITH_UNKNOWN: import("@/features/admin/document-types/custom-fields-api").ImportPreviewResponse =
  {
    import_session_id: "sess-002",
    rows_total: 15,
    known_columns: [{ header: "Компания", mapped_to: "asset_name" }],
    unknown_columns: [
      {
        header: "ИНН организации",
        sample_values: ["7736578876", "7842349885"],
        suggested_type: "text",
      },
    ],
  };

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ImportXlsxDialog — Step 1 (upload)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseDocumentTypes.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
  });

  it("test_step1_renders_upload_button", () => {
    renderDialog();
    expect(screen.getByTestId("import-upload-btn")).toBeInTheDocument();
    expect(screen.getByTestId("import-file-input")).toBeInTheDocument();
  });

  it("test_step1_upload_btn_disabled_without_file", () => {
    renderDialog();
    expect(screen.getByTestId("import-upload-btn")).toBeDisabled();
  });

  it("test_step1_413_shows_too_large_error", async () => {
    const user = userEvent.setup();
    const Err = CustomFieldApiResponseError as new (
      status: number,
      detail: string,
      code?: string,
    ) => InstanceType<typeof CustomFieldApiResponseError>;
    mockPreview.mockRejectedValueOnce(new Err(413, "too large"));

    renderDialog();

    const file = new File(["hello"], "test.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    const fileInput = screen.getByTestId("import-file-input");
    await userEvent.upload(fileInput, file);

    await user.click(screen.getByTestId("import-upload-btn"));

    expect(await screen.findByText(/Файл слишком большой/i)).toBeInTheDocument();
  });

  it("test_step1_422_shows_headers_error", async () => {
    const user = userEvent.setup();
    const Err = CustomFieldApiResponseError as new (
      status: number,
      detail: string,
      code?: string,
    ) => InstanceType<typeof CustomFieldApiResponseError>;
    mockPreview.mockRejectedValueOnce(new Err(422, "no headers"));

    renderDialog();

    const file = new File(["bad"], "bad.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await userEvent.upload(screen.getByTestId("import-file-input"), file);
    await user.click(screen.getByTestId("import-upload-btn"));

    expect(await screen.findByText(/первая строка содержит заголовки/i)).toBeInTheDocument();
  });

  it("test_step1_success_with_no_unknown_columns_goes_to_step2", async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValueOnce(PREVIEW_NO_UNKNOWN);

    renderDialog();

    const file = new File(["data"], "good.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await userEvent.upload(screen.getByTestId("import-file-input"), file);
    await user.click(screen.getByTestId("import-upload-btn"));

    // Step 2: should show recognized info box
    expect(await screen.findByText(/Компания/)).toBeInTheDocument();
    expect(screen.getByText(/42 строк/i)).toBeInTheDocument();

    // No unknown columns message
    expect(screen.getByText(/Все колонки распознаны/i)).toBeInTheDocument();

    // Import button should be visible
    expect(screen.getByTestId("import-confirm-btn")).toBeInTheDocument();
  });
});

describe("ImportXlsxDialog — Step 2 (decisions + confirm)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseDocumentTypes.mockReturnValue({
      data: [
        {
          code: "contract",
          display_name: "Договор",
          pre_notice_days: [30],
          notify_in_day: false,
          overdue_every_days: 7,
          created_at: "",
          updated_at: "",
        },
      ],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
  });

  it("test_step2_unknown_column_cards_are_rendered", async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValueOnce(PREVIEW_WITH_UNKNOWN);

    renderDialog();
    const file = new File(["data"], "test.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await userEvent.upload(screen.getByTestId("import-file-input"), file);
    await user.click(screen.getByTestId("import-upload-btn"));

    expect(await screen.findByTestId("unknown-col-card-ИНН организации")).toBeInTheDocument();
    expect(screen.getByText(/ИНН организации/)).toBeInTheDocument();
    // Sample values should appear
    expect(screen.getByText(/7736578876/)).toBeInTheDocument();
  });

  it("test_step2_confirm_with_all_skipped_calls_import_confirm", async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValueOnce(PREVIEW_WITH_UNKNOWN);
    mockConfirm.mockResolvedValueOnce({ rows_imported: 15, rows_failed: 0, fields_added: 0 });

    renderDialog();
    const file = new File(["data"], "test.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await userEvent.upload(screen.getByTestId("import-file-input"), file);
    await user.click(screen.getByTestId("import-upload-btn"));

    await screen.findByTestId("unknown-col-card-ИНН организации");

    // Default action is "skip" — so all decisions are complete
    // Enter TOTP
    await user.type(screen.getByTestId("import-totp-input"), "654321");

    const confirmBtn = screen.getByTestId("import-confirm-btn");
    await user.click(confirmBtn);

    await waitFor(() => {
      expect(mockConfirm).toHaveBeenCalledWith(
        "sess-002",
        expect.arrayContaining([
          expect.objectContaining({ header: "ИНН организации", action: "skip" }),
        ]),
        "654321",
      );
    });
  });

  it("test_step2_confirm_disabled_without_totp", async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValueOnce(PREVIEW_NO_UNKNOWN);

    renderDialog();
    const file = new File(["data"], "test.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await userEvent.upload(screen.getByTestId("import-file-input"), file);
    await user.click(screen.getByTestId("import-upload-btn"));

    await screen.findByTestId("import-confirm-btn");
    expect(screen.getByTestId("import-confirm-btn")).toBeDisabled();
  });

  it("test_step2_success_shows_result_summary", async () => {
    const user = userEvent.setup();
    mockPreview.mockResolvedValueOnce(PREVIEW_NO_UNKNOWN);
    mockConfirm.mockResolvedValueOnce({ rows_imported: 42, rows_failed: 0, fields_added: 2 });

    renderDialog();
    const file = new File(["data"], "test.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await userEvent.upload(screen.getByTestId("import-file-input"), file);
    await user.click(screen.getByTestId("import-upload-btn"));

    await screen.findByTestId("import-confirm-btn");
    await user.type(screen.getByTestId("import-totp-input"), "999888");
    await user.click(screen.getByTestId("import-confirm-btn"));

    expect(await screen.findByText(/Импорт завершён/i)).toBeInTheDocument();
    expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ variant: "success" }));
  });
});
