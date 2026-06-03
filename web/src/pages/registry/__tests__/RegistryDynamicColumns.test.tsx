// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for RegistryPage — dynamic custom field columns (Phase 3, US-9)
 *
 * Requirements:
 *   - When a document type has N custom fields, N extra columns appear in the table
 *   - When filtered by type_code, only that type's fields appear
 *   - Cell renderers work for text, number, date, enum types
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mocks (same set as RegistryPage.test.tsx)
// ---------------------------------------------------------------------------

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: () => ({
    getVirtualItems: () => [],
    getTotalSize: () => 0,
    scrollToIndex: vi.fn(),
  }),
}));

vi.mock("@tanstack/react-router", () => ({
  useLocation: () => ({ search: {}, pathname: "/registry" }),
  useNavigate: () => vi.fn(),
}));

vi.mock("date-fns", () => ({
  format: (_d: Date, fmt: string) => `formatted-${fmt}`,
  parseISO: (s: string) => new Date(s),
}));

vi.mock("date-fns/locale", () => ({ ru: {} }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

vi.mock("@/features/auth/AuthProvider", () => ({
  useAuth: vi.fn(),
}));

vi.mock("@/features/registry/hooks/useDocuments", () => ({
  useDocuments: vi.fn(),
}));

vi.mock("@/features/registry/hooks/useDocumentTypes", () => ({
  useDocumentTypes: vi.fn(),
  DOCUMENT_TYPES_QUERY_KEY: "document-types",
}));

vi.mock("@/features/registry/hooks/useDocumentMutations", () => ({
  useBulkArchiveDocuments: () => ({ mutate: vi.fn(), isPending: false }),
  usePatchDocument: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useArchiveDocument: () => ({ mutate: vi.fn(), isPending: false }),
  useRestoreDocument: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateDocument: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  BULK_ARCHIVE_MAX: 100,
}));

vi.mock("@/features/registry/hooks/useExportJob", () => ({
  useRequestExportJob: () => ({ mutate: vi.fn(), isPending: false }),
  useExportJobs: () => ({ data: [], isLoading: false, isError: false, refetch: vi.fn() }),
  useDownloadExportJob: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/features/registry/hooks/useAssets", () => ({
  useActiveAssets: () => ({ data: [], isLoading: false }),
}));

vi.mock("@/features/registry/hooks/useHistory", () => ({
  useHistory: () => ({ data: [], isLoading: false, isError: false, error: null }),
}));

vi.mock("@/features/registry/hooks/useAttachments", () => ({
  useAttachments: () => ({ data: [], isLoading: false, isError: false, error: null }),
  useUploadAttachment: () => ({ uploads: [], upload: vi.fn(), clearDone: vi.fn() }),
  useDeleteAttachment: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/features/registry/computeStatus", () => ({
  computeStatus: () => "ok" as const,
}));

// ---------------------------------------------------------------------------
// Imports after mocks
// ---------------------------------------------------------------------------

import { useAuth } from "@/features/auth/AuthProvider";
import { useDocuments } from "@/features/registry/hooks/useDocuments";
import { useDocumentTypes } from "@/features/registry/hooks/useDocumentTypes";

const mockUseAuth = vi.mocked(useAuth);
const mockUseDocuments = vi.mocked(useDocuments);
const mockUseDocumentTypes = vi.mocked(useDocumentTypes);

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const ADMIN_USER = { sub: "u-admin", email: "admin@example.com", role: "admin" };

const CONTRACT_TYPE = {
  code: "contract",
  display_name: "Договор",
  pre_notice_days: [30, 7],
  notify_in_day: false,
  overdue_every_days: 7,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  custom_field_schema: [
    {
      key: "partner_inn",
      display_name: "ИНН партнёра",
      type: "text" as const,
      required: false,
      options: null,
    },
    {
      key: "contract_value",
      display_name: "Сумма договора",
      type: "number" as const,
      required: false,
      options: null,
    },
  ],
};

const LICENSE_TYPE = {
  code: "license",
  display_name: "Лицензия",
  pre_notice_days: [30],
  notify_in_day: true,
  overdue_every_days: 14,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  custom_field_schema: [
    {
      key: "license_authority",
      display_name: "Лицензирующий орган",
      type: "text" as const,
      required: true,
      options: null,
    },
  ],
};

function makeDocument(overrides: Record<string, unknown> = {}) {
  return {
    id: crypto.randomUUID(),
    asset_id: crypto.randomUUID(),
    asset_name: "ООО Тест",
    type_code: "contract",
    type_display_name: "Договор",
    number: "ДГ-2026-001",
    issue_date: "2026-01-01",
    expiry_date: "2027-06-01",
    responsible_user_id: null,
    responsible_user_name: null,
    status: "ok" as const,
    notes: null,
    created_at: "2026-05-07T10:00:00Z",
    updated_at: "2026-05-07T10:00:00Z",
    deleted_at: null,
    custom_field_values: {},
    ...overrides,
  };
}

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function withQC(ui: React.ReactElement) {
  const qc = makeQC();
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("RegistryPage — dynamic custom field columns", () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({ claims: ADMIN_USER } as any);
  });

  it("test_no_custom_fields_when_doc_types_have_no_schema", async () => {
    mockUseDocumentTypes.mockReturnValue({
      data: [{ ...CONTRACT_TYPE, custom_field_schema: undefined }],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseDocuments.mockReturnValue({
      data: { items: [makeDocument()], total: 1 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    const { RegistryPage } = await import("../RegistryPage");
    render(withQC(<RegistryPage />));

    // Standard columns should be present
    expect(screen.getByText(/registry\.col_counterparty/i)).toBeInTheDocument();

    // No dynamic column headers
    expect(screen.queryByText("ИНН партнёра")).not.toBeInTheDocument();
    expect(screen.queryByText("Сумма договора")).not.toBeInTheDocument();
  });

  it("test_dynamic_columns_appear_when_type_has_schema", async () => {
    mockUseDocumentTypes.mockReturnValue({
      data: [CONTRACT_TYPE],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseDocuments.mockReturnValue({
      data: {
        items: [
          makeDocument({
            custom_field_values: { partner_inn: "7736578876", contract_value: 1500000 },
          }),
        ],
        total: 1,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    const { RegistryPage } = await import("../RegistryPage");
    render(withQC(<RegistryPage />));

    // Dynamic column headers should appear
    expect(await screen.findByText("ИНН партнёра")).toBeInTheDocument();
    expect(screen.getByText("Сумма договора")).toBeInTheDocument();
  });

  it("test_union_of_fields_when_no_type_filter_applied", async () => {
    mockUseDocumentTypes.mockReturnValue({
      data: [CONTRACT_TYPE, LICENSE_TYPE],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseDocuments.mockReturnValue({
      data: { items: [makeDocument()], total: 1 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    const { RegistryPage } = await import("../RegistryPage");
    render(withQC(<RegistryPage />));

    // All 3 unique fields from both types should be visible
    expect(await screen.findByText("ИНН партнёра")).toBeInTheDocument();
    expect(screen.getByText("Сумма договора")).toBeInTheDocument();
    expect(screen.getByText("Лицензирующий орган")).toBeInTheDocument();
  });
});
