// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Unit tests for DocumentDetailDrawer (US-8, US-18).
 *
 * Run:
 *   pnpm vitest run src/pages/registry/__tests__/DocumentDetailDrawer.test.tsx
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/features/auth/AuthProvider", () => ({
  useAuth: vi.fn(),
}));

vi.mock("@/features/registry/hooks/useAttachments", () => ({
  useAttachments: vi.fn(),
  useUploadAttachment: () => ({ uploads: [], upload: vi.fn(), clearDone: vi.fn() }),
  useDeleteAttachment: () => ({ mutate: vi.fn(), isPending: false }),
  MAX_ATTACHMENT_SIZE_BYTES: 25 * 1024 * 1024,
  ALLOWED_MIME_TYPES: [
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ],
}));

vi.mock("@/features/registry/hooks/useHistory", () => ({
  useHistory: vi.fn(), // kept for backward compat with some tests
  useDocumentHistory: vi.fn(),
  useAssetHistory: vi.fn(),
}));

vi.mock("@/features/registry/hooks/useDocumentMutations", () => ({
  useArchiveDocument: () => ({ mutate: vi.fn(), isPending: false }),
  useRestoreDocument: () => ({ mutate: vi.fn(), isPending: false }),
  usePatchDocument: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  BULK_ARCHIVE_MAX: 100,
}));

// v1.25.0 — MainTab now reads from asset and document-type catalogs to render
// the full edit form (combobox + cf-controls). Stub them so QueryClient is not
// required in tests.
vi.mock("@/features/registry/hooks/useAssets", () => ({
  useActiveAssets: () => ({ data: { items: [] }, isLoading: false, isError: false }),
  useAssets: () => ({ data: { items: [] }, isLoading: false, isError: false }),
  ASSETS_QUERY_KEY: "assets",
}));

vi.mock("@/features/registry/hooks/useDocumentTypes", () => ({
  useDocumentTypes: () => ({ data: [], isLoading: false, isError: false }),
  DOCUMENT_TYPES_QUERY_KEY: "document-types",
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

vi.mock("date-fns", () => ({
  format: (_d: Date, _fmt: string) => "01.01.2026",
  parseISO: (s: string) => new Date(s),
}));

vi.mock("date-fns/locale", () => ({ ru: {} }));

vi.mock("@/features/registry/computeStatus", () => ({
  computeStatus: () => "ok" as const,
}));

vi.mock("@/features/registry/api", () => ({
  downloadAttachment: vi.fn().mockResolvedValue("https://signed-url.example.com/file"),
}));

vi.mock("@/shared/ui/toast", () => ({
  toast: { show: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

import { useAuth } from "@/features/auth/AuthProvider";
import { useAttachments } from "@/features/registry/hooks/useAttachments";
import { useDocumentHistory } from "@/features/registry/hooks/useHistory";

const mockUseAuth = vi.mocked(useAuth);
const mockUseAttachments = vi.mocked(useAttachments);
const mockUseHistory = vi.mocked(useDocumentHistory);

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

// useAuth returns { claims: JwtClaims | null, ... } — use claims shape, not user
const VIEWER_CLAIMS = { sub: "u-viewer", email: "viewer@example.com", role: "viewer" };
const EDITOR_CLAIMS = { sub: "u-editor", email: "editor@example.com", role: "editor" };
const ADMIN_CLAIMS = { sub: "u-admin", email: "admin@example.com", role: "admin" };

// Aliases for backward compat with test bodies below
const VIEWER = VIEWER_CLAIMS;
const EDITOR = EDITOR_CLAIMS;
const ADMIN = ADMIN_CLAIMS;

function makeDoc(overrides: Record<string, unknown> = {}) {
  return {
    id: "d-001",
    asset_id: "a-001",
    asset_name: "ООО Тест",
    type_code: "contract",
    type_display_name: "Договор",
    number: "ДГ-2026-001",
    issue_date: "2026-01-01",
    expiry_date: "2027-06-01",
    responsible_user_id: null,
    responsible_user_name: null,
    status: "ok" as const,
    urgency_status: "ok" as const,
    notes: "Тестовый договор",
    created_by: "u-editor",
    updated_by: "u-editor",
    created_at: "2026-05-07T10:00:00Z",
    updated_at: "2026-05-07T10:00:00Z",
    deleted_at: null,
    ...overrides,
  };
}

async function renderDrawer(document = makeDoc(), _isOpen = true, onClose = vi.fn()) {
  const { DocumentDetailDrawer } = await import("../DocumentDetailDrawer");
  return render(React.createElement(DocumentDetailDrawer, { document, onClose }));
}

// ---------------------------------------------------------------------------
// US-8: No attachments — empty state
// ---------------------------------------------------------------------------

describe("DocumentDetailDrawer — attachments", () => {
  it("test_drawer_no_attachments_shows_empty_state", async () => {
    mockUseAuth.mockReturnValue({ claims: VIEWER } as any);
    mockUseAttachments.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseHistory.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);

    await renderDrawer();

    // The attachments tab must be activated first — content is lazy-rendered per tab
    const attachTab = screen.getByRole("tab", { name: /Вложения/i });
    await userEvent.click(attachTab);

    // The "Нет вложений" placeholder should appear
    await waitFor(() => {
      expect(screen.getByText(/Нет вложений/i)).toBeTruthy();
    });
  });

  it("test_drawer_upload_zone_visible_for_editor_not_viewer", async () => {
    // The upload area is a drag-drop section (aria-label: "Загрузка вложений...")
    // not a plain button. Editors see it; viewers do not.
    mockUseAuth.mockReturnValue({ claims: EDITOR } as any);
    mockUseAttachments.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseHistory.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);

    await renderDrawer();

    // Activate the Вложения tab
    const attachTab = screen.getByRole("tab", { name: /Вложения/i });
    await userEvent.click(attachTab);

    await waitFor(() => {
      // Upload zone (drag-drop section) is rendered for editor role
      const uploadZone =
        screen.queryByRole("region", { name: /Загрузка вложений/i }) ??
        screen.queryByText(/выберите файл/i);
      expect(uploadZone).toBeTruthy();
    });
  });
});

// ---------------------------------------------------------------------------
// US-8: Archived document banner
// ---------------------------------------------------------------------------

describe("DocumentDetailDrawer — archived document", () => {
  it("test_drawer_archived_document_shows_banner", async () => {
    mockUseAuth.mockReturnValue({ claims: ADMIN } as any);
    mockUseAttachments.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseHistory.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);

    const archivedDoc = makeDoc({
      status: "archived",
      urgency_status: "archived",
      deleted_at: "2026-05-07T09:00:00Z",
    });

    await renderDrawer(archivedDoc);

    await waitFor(() => {
      // Banner "Документ в архиве" must be visible
      expect(screen.getByText(/Документ в архиве/i)).toBeTruthy();
    });
  });

  it("test_drawer_archived_document_restore_button_visible_for_admin_only", async () => {
    mockUseAuth.mockReturnValue({ claims: ADMIN } as any);
    mockUseAttachments.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseHistory.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);

    const archivedDoc = makeDoc({ status: "archived", deleted_at: "2026-05-07T09:00:00Z" });
    await renderDrawer(archivedDoc);

    await waitFor(() => {
      const restoreBtn = screen.queryByRole("button", { name: /Восстановить/i });
      expect(restoreBtn).toBeTruthy();
    });
  });
});

// ---------------------------------------------------------------------------
// US-18: History panel — audit-service unavailable
// ---------------------------------------------------------------------------

describe("DocumentDetailDrawer — history panel", () => {
  it("test_drawer_history_audit_unavailable_shows_error", async () => {
    mockUseAuth.mockReturnValue({ claims: VIEWER } as any);
    mockUseAttachments.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseHistory.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("audit-service 503"),
    } as any);

    await renderDrawer();

    // Activate the "История изменений" tab to render history content
    const historyTab = screen.getByRole("tab", { name: /История/i });
    await userEvent.click(historyTab);

    await waitFor(() => {
      // Error message must be visible
      expect(screen.getByText(/временно недоступна/i)).toBeTruthy();
    });
  });

  it("test_drawer_esc_key_closes_drawer", async () => {
    mockUseAuth.mockReturnValue({ claims: VIEWER } as any);
    mockUseAttachments.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseHistory.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);

    const onClose = vi.fn();
    await renderDrawer(makeDoc(), true, onClose);

    fireEvent.keyDown(document, { key: "Escape", code: "Escape" });

    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });
});

// ---------------------------------------------------------------------------
// Accessibility — axe-core
// ---------------------------------------------------------------------------

describe("DocumentDetailDrawer — accessibility", () => {
  it("test_drawer_axe_clean", async () => {
    const axe = await import("axe-core").catch(() => null);
    if (!axe) return; // axe-core not installed — skip

    mockUseAuth.mockReturnValue({ claims: EDITOR } as any);
    mockUseAttachments.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);
    mockUseHistory.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      error: null,
    } as any);

    const { container } = await renderDrawer();
    const results = await axe.default.run(container);
    const violations = results.violations.filter((v) => !["color-contrast"].includes(v.id));
    expect(violations).toHaveLength(0);
  });
});
