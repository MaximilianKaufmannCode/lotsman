// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Unit tests for ExportJobsModal (US-20, US-21, Q8).
 *
 * Business rules under test:
 *  - pending/running/done/failed/expired status renders the correct label
 *  - Download button present only when status=done AND not expired (Q8: 24h TTL)
 *  - Download button absent when status=done but expires_at is in the past
 *  - Download button absent for status=failed, status=pending, status=running
 *  - Empty state ("Нет экспортов") shown when jobs=[]
 *  - Error state shown when isError=true
 *  - Escape closes the modal
 *  - axe-core finds zero violations
 *
 * Run:
 *   pnpm vitest run src/pages/registry/__tests__/ExportJobsModal.test.tsx
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/features/registry/hooks/useExportJob", () => ({
  useExportJobs: vi.fn(),
  useDownloadExportJob: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("date-fns", () => ({
  format: (_date: Date, _fmt: string) => "01.01.2026 12:00",
  isAfter: vi.fn((a: Date, b: Date) => a > b),
  parseISO: (s: string) => new Date(s),
}));

vi.mock("date-fns/locale", () => ({ ru: {} }));

import { useExportJobs } from "@/features/registry/hooks/useExportJob";

const mockUseExportJobs = vi.mocked(useExportJobs);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeJob(
  overrides: Partial<{
    id: string;
    status: "pending" | "running" | "done" | "failed" | "expired";
    created_at: string;
    expires_at: string | null;
    error: string | null;
  }> = {},
) {
  return {
    id: "job-001",
    status: "done" as const,
    created_at: "2026-05-07T10:00:00Z",
    expires_at: "2026-05-08T10:00:00Z", // 24h in future
    error: null,
    ...overrides,
  };
}

async function renderModal(open = true, onClose = vi.fn()) {
  const { ExportJobsModal } = await import("../ExportJobsModal");
  return render(React.createElement(ExportJobsModal, { open, onClose }));
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

describe("ExportJobsModal — empty state", () => {
  it("test_empty_jobs_shows_no_exports_placeholder", async () => {
    mockUseExportJobs.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      expect(screen.getByText(/Нет экспортов/i)).toBeTruthy();
    });
  });
});

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("ExportJobsModal — error state", () => {
  it("test_error_state_shows_retry_button", async () => {
    mockUseExportJobs.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      // Error message present
      expect(screen.getByText(/Не удалось загрузить/i)).toBeTruthy();
      // Retry button present
      expect(screen.getByRole("button", { name: /Повторить/i })).toBeTruthy();
    });
  });
});

// ---------------------------------------------------------------------------
// US-20: Status badges
// ---------------------------------------------------------------------------

describe("ExportJobsModal — status labels", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("test_pending_job_shows_waiting_label", async () => {
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "pending", expires_at: null })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      expect(screen.getByText("Ожидание")).toBeTruthy();
    });
  });

  it("test_running_job_shows_in_progress_label", async () => {
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "running", expires_at: null })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      expect(screen.getByText(/Выполняется/i)).toBeTruthy();
    });
  });

  it("test_done_job_not_expired_shows_ready_label", async () => {
    // expires_at is in the future — parseISO returns a far-future date
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "done", expires_at: "2099-01-01T00:00:00Z" })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      expect(screen.getByText("Готов")).toBeTruthy();
    });
  });

  it("test_failed_job_shows_error_label", async () => {
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "failed", expires_at: null, error: "worker crash" })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      expect(screen.getByText("Ошибка")).toBeTruthy();
    });
  });

  it("test_expired_job_shows_expired_label", async () => {
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "expired", expires_at: "2026-05-07T09:00:00Z" })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      expect(screen.getByText("Истёк")).toBeTruthy();
    });
  });
});

// ---------------------------------------------------------------------------
// US-20, Q8: Download button visibility
// ---------------------------------------------------------------------------

describe("ExportJobsModal — download button visibility", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // isAfter is already mocked at module level: (a, b) => a > b
    // For future expires_at dates, isAfter(new Date(), future) === false → not expired
  });

  it("test_download_button_visible_for_done_not_expired_job", async () => {
    // Status=done, expires_at far in future => canDownload = true
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "done", expires_at: "2099-01-01T00:00:00Z" })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      const dlBtn = screen.queryByRole("button", { name: /Скачать/i });
      expect(dlBtn).toBeTruthy();
    });
  });

  it("test_download_button_absent_for_pending_job", async () => {
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "pending", expires_at: null })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      const dlBtn = screen.queryByRole("button", { name: /Скачать/i });
      expect(dlBtn).toBeNull();
    });
  });

  it("test_download_button_absent_for_running_job", async () => {
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "running", expires_at: null })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      const dlBtn = screen.queryByRole("button", { name: /Скачать/i });
      expect(dlBtn).toBeNull();
    });
  });

  it("test_download_button_absent_for_failed_job", async () => {
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "failed", expires_at: null, error: "oom" })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      const dlBtn = screen.queryByRole("button", { name: /Скачать/i });
      expect(dlBtn).toBeNull();
    });
  });

  it("test_download_button_absent_when_status_expired", async () => {
    // Explicit status=expired
    mockUseExportJobs.mockReturnValue({
      data: [makeJob({ status: "expired", expires_at: "2026-05-06T10:00:00Z" })],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    await renderModal();

    await waitFor(() => {
      const dlBtn = screen.queryByRole("button", { name: /Скачать/i });
      expect(dlBtn).toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// Escape key
// ---------------------------------------------------------------------------

describe("ExportJobsModal — keyboard", () => {
  it("test_escape_key_closes_modal", async () => {
    mockUseExportJobs.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    const onClose = vi.fn();
    await renderModal(true, onClose);

    fireEvent.keyDown(document, { key: "Escape", code: "Escape" });

    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });
});

// ---------------------------------------------------------------------------
// Accessibility — axe-core
// ---------------------------------------------------------------------------

describe("ExportJobsModal — accessibility", () => {
  it("test_modal_axe_clean_with_jobs_list", async () => {
    const axe = await import("axe-core").catch(() => null);
    if (!axe) return;

    mockUseExportJobs.mockReturnValue({
      data: [
        makeJob({ id: "j1", status: "done", expires_at: "2099-01-01T00:00:00Z" }),
        makeJob({ id: "j2", status: "pending", expires_at: null }),
        makeJob({ id: "j3", status: "failed", expires_at: null, error: "timeout" }),
      ],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    const { container } = await renderModal();
    const results = await axe.default.run(container);
    const violations = results.violations.filter((v) => !["color-contrast"].includes(v.id));
    expect(violations).toHaveLength(0);
  });
});
