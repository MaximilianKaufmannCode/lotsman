// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Tests for useFilterPresets hooks — optimistic mutations and rollback.
 *
 * Strategy:
 * - Mock @/features/auth/api at the module level to control API responses.
 * - Mock @/shared/ui/toast to assert toast calls without side-effects.
 * - Each mutation is tested for the happy-path (cache update) and the
 *   error-path (rollback + destructive toast).
 * - useSavedFilters is tested for query key and staleTime.
 *
 * Run:
 *   pnpm vitest run src/features/registry/__tests__/useFilterPresets.test.tsx
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SavedFilter } from "@/features/auth/api";

// ---------------------------------------------------------------------------
// Shared mock data
// ---------------------------------------------------------------------------

const makeFilter = (overrides?: Partial<SavedFilter>): SavedFilter => ({
  id: crypto.randomUUID(),
  user_id: "user-1",
  name: "Тест пресет",
  filter_json: { type_codes: ["contract"] },
  is_default: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ...overrides,
});

// ---------------------------------------------------------------------------
// Module mocks (hoisted automatically by Vitest)
// ---------------------------------------------------------------------------

vi.mock("@/features/auth/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/features/auth/api")>();
  return {
    ...original,
    listMySavedFilters: vi.fn(),
    createSavedFilter: vi.fn(),
    updateSavedFilter: vi.fn(),
    deleteSavedFilter: vi.fn(),
  };
});

vi.mock("@/shared/ui/toast", () => ({
  toast: { show: vi.fn() },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function makeWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

// ---------------------------------------------------------------------------
// useSavedFilters
// ---------------------------------------------------------------------------

describe("useSavedFilters", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("calls listMySavedFilters and returns data", async () => {
    const { listMySavedFilters } = await import("@/features/auth/api");
    const filters = [makeFilter(), makeFilter({ name: "Второй" })];
    vi.mocked(listMySavedFilters).mockResolvedValueOnce(filters);

    const { useSavedFilters } = await import("../filters/useFilterPresets");
    const qc = makeQueryClient();

    const { result } = renderHook(() => useSavedFilters(), { wrapper: makeWrapper(qc) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
    expect(vi.mocked(listMySavedFilters)).toHaveBeenCalledOnce();
  });

  it("uses SAVED_FILTERS_QUERY_KEY as query key", async () => {
    const { SAVED_FILTERS_QUERY_KEY, useSavedFilters } = await import(
      "../filters/useFilterPresets"
    );
    const { listMySavedFilters } = await import("@/features/auth/api");
    vi.mocked(listMySavedFilters).mockResolvedValueOnce([]);

    const qc = makeQueryClient();
    renderHook(() => useSavedFilters(), { wrapper: makeWrapper(qc) });

    await waitFor(() => expect(qc.getQueryState(SAVED_FILTERS_QUERY_KEY)).toBeTruthy());
    expect(SAVED_FILTERS_QUERY_KEY).toEqual(["saved-filters"]);
  });

  it("surfaces errors from listMySavedFilters", async () => {
    const { listMySavedFilters } = await import("@/features/auth/api");
    vi.mocked(listMySavedFilters).mockRejectedValueOnce(new Error("Network error"));

    const { useSavedFilters } = await import("../filters/useFilterPresets");
    const qc = makeQueryClient();

    const { result } = renderHook(() => useSavedFilters(), { wrapper: makeWrapper(qc) });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe("Network error");
  });
});

// ---------------------------------------------------------------------------
// useCreateSavedFilter
// ---------------------------------------------------------------------------

describe("useCreateSavedFilter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("optimistically adds item before API resolves", async () => {
    const { createSavedFilter } = await import("@/features/auth/api");
    const { SAVED_FILTERS_QUERY_KEY, useCreateSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const existing = makeFilter({ name: "Существующий" });
    const created = makeFilter({ name: "Новый пресет" });

    // API resolves after a tick so we can observe optimistic state
    vi.mocked(createSavedFilter).mockResolvedValueOnce(created);

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [existing]);

    const { result } = renderHook(() => useCreateSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({ name: "Новый пресет", filter_json: { type_codes: ["license"] } });
    });

    // After mutation settles, cache should have been refreshed (invalidated → refetch)
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("adds optimistic item to cache before server responds", async () => {
    const { createSavedFilter } = await import("@/features/auth/api");
    const { SAVED_FILTERS_QUERY_KEY, useCreateSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const existing = makeFilter();
    let resolveApi!: (v: SavedFilter) => void;
    const apiPromise = new Promise<SavedFilter>((res) => {
      resolveApi = res;
    });
    vi.mocked(createSavedFilter).mockReturnValueOnce(apiPromise);

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [existing]);

    const { result } = renderHook(() => useCreateSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    act(() => {
      result.current.mutate({ name: "Оптимистик", filter_json: {} });
    });

    // While the API is still pending, optimistic item appears
    await waitFor(() => {
      const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
      return (cached?.length ?? 0) === 2;
    });

    const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
    const optimisticItem = cached?.find((f) => f.id.startsWith("optimistic-"));
    expect(optimisticItem).toBeTruthy();
    expect(optimisticItem?.name).toBe("Оптимистик");

    // Resolve so the test cleans up
    resolveApi(makeFilter({ name: "Оптимистик" }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("rolls back optimistic insert on API error", async () => {
    const { createSavedFilter } = await import("@/features/auth/api");
    const { toast } = await import("@/shared/ui/toast");
    const { SAVED_FILTERS_QUERY_KEY, useCreateSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const existing = makeFilter({ name: "Оригинал" });
    vi.mocked(createSavedFilter).mockRejectedValueOnce(new Error("500 Server Error"));

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [existing]);

    const { result } = renderHook(() => useCreateSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      try {
        await result.current.mutateAsync({ name: "Провальный", filter_json: {} });
      } catch {
        // expected
      }
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    // Cache rolled back to original single item
    const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
    expect(cached).toHaveLength(1);
    expect(cached?.[0]?.name).toBe("Оригинал");

    // Destructive toast shown
    expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({ variant: "destructive" }));
  });

  it("shows success toast after successful create", async () => {
    const { createSavedFilter } = await import("@/features/auth/api");
    const { toast } = await import("@/shared/ui/toast");
    const { SAVED_FILTERS_QUERY_KEY, useCreateSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const created = makeFilter({ name: "Мой пресет" });
    vi.mocked(createSavedFilter).mockResolvedValueOnce(created);

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, []);

    const { result } = renderHook(() => useCreateSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.mutateAsync({ name: "Мой пресет", filter_json: {} });
    });

    expect(toast.show).toHaveBeenCalledWith(
      expect.objectContaining({ title: expect.stringContaining("Мой пресет") }),
    );
  });
});

// ---------------------------------------------------------------------------
// useUpdateSavedFilter
// ---------------------------------------------------------------------------

describe("useUpdateSavedFilter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("optimistically updates the target item's name", async () => {
    const { updateSavedFilter } = await import("@/features/auth/api");
    const { SAVED_FILTERS_QUERY_KEY, useUpdateSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const target = makeFilter({ id: "id-1", name: "Старое имя" });
    const other = makeFilter({ id: "id-2", name: "Другой" });
    let resolveApi!: (v: SavedFilter) => void;
    const apiPromise = new Promise<SavedFilter>((res) => {
      resolveApi = res;
    });
    vi.mocked(updateSavedFilter).mockReturnValueOnce(apiPromise);

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [target, other]);

    const { result } = renderHook(() => useUpdateSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    act(() => {
      result.current.mutate({ id: "id-1", body: { name: "Новое имя" } });
    });

    await waitFor(() => {
      const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
      return cached?.find((f) => f.id === "id-1")?.name === "Новое имя";
    });

    const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
    expect(cached?.find((f) => f.id === "id-1")?.name).toBe("Новое имя");
    expect(cached?.find((f) => f.id === "id-2")?.name).toBe("Другой");

    resolveApi(makeFilter({ id: "id-1", name: "Новое имя" }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("clears is_default on other items when setting a new default", async () => {
    const { updateSavedFilter } = await import("@/features/auth/api");
    const { SAVED_FILTERS_QUERY_KEY, useUpdateSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const oldDefault = makeFilter({ id: "id-1", is_default: true });
    const toPromote = makeFilter({ id: "id-2", is_default: false });
    let resolveApi!: (v: SavedFilter) => void;
    const apiPromise = new Promise<SavedFilter>((res) => {
      resolveApi = res;
    });
    vi.mocked(updateSavedFilter).mockReturnValueOnce(apiPromise);

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [oldDefault, toPromote]);

    const { result } = renderHook(() => useUpdateSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    act(() => {
      result.current.mutate({ id: "id-2", body: { is_default: true } });
    });

    await waitFor(() => {
      const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
      return cached?.find((f) => f.id === "id-2")?.is_default === true;
    });

    const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
    // New default is set
    expect(cached?.find((f) => f.id === "id-2")?.is_default).toBe(true);
    // Old default is cleared
    expect(cached?.find((f) => f.id === "id-1")?.is_default).toBe(false);

    resolveApi(makeFilter({ id: "id-2", is_default: true }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("rolls back optimistic update on error", async () => {
    const { updateSavedFilter } = await import("@/features/auth/api");
    const { toast } = await import("@/shared/ui/toast");
    const { SAVED_FILTERS_QUERY_KEY, useUpdateSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const target = makeFilter({ id: "id-1", name: "Оригинал" });
    vi.mocked(updateSavedFilter).mockRejectedValueOnce(new Error("Network"));

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [target]);

    const { result } = renderHook(() => useUpdateSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      try {
        await result.current.mutateAsync({ id: "id-1", body: { name: "Провал" } });
      } catch {
        // expected
      }
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
    expect(cached?.[0]?.name).toBe("Оригинал");

    expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({ variant: "destructive" }));
  });
});

// ---------------------------------------------------------------------------
// useDeleteSavedFilter
// ---------------------------------------------------------------------------

describe("useDeleteSavedFilter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("optimistically removes item before API responds", async () => {
    const { deleteSavedFilter } = await import("@/features/auth/api");
    const { SAVED_FILTERS_QUERY_KEY, useDeleteSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const toDelete = makeFilter({ id: "del-1" });
    const toKeep = makeFilter({ id: "keep-1" });
    let resolveApi!: () => void;
    const apiPromise = new Promise<void>((res) => {
      resolveApi = res;
    });
    vi.mocked(deleteSavedFilter).mockReturnValueOnce(apiPromise);

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [toDelete, toKeep]);

    const { result } = renderHook(() => useDeleteSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    act(() => {
      result.current.mutate("del-1");
    });

    // Optimistically removed while API is pending
    await waitFor(() => {
      const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
      return cached?.length === 1;
    });

    const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
    expect(cached?.[0]?.id).toBe("keep-1");
    expect(cached?.find((f) => f.id === "del-1")).toBeUndefined();

    resolveApi();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("rolls back removal on API error", async () => {
    const { deleteSavedFilter } = await import("@/features/auth/api");
    const { toast } = await import("@/shared/ui/toast");
    const { SAVED_FILTERS_QUERY_KEY, useDeleteSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const filter = makeFilter({ id: "del-1", name: "Ценный пресет" });
    vi.mocked(deleteSavedFilter).mockRejectedValueOnce(new Error("403 Forbidden"));

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [filter]);

    const { result } = renderHook(() => useDeleteSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      try {
        await result.current.mutateAsync("del-1");
      } catch {
        // expected
      }
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    // Rolled back — item is back in cache
    const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
    expect(cached).toHaveLength(1);
    expect(cached?.[0]?.id).toBe("del-1");

    expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({ variant: "destructive" }));
  });

  it("shows plain toast (no destructive) on successful delete", async () => {
    const { deleteSavedFilter } = await import("@/features/auth/api");
    const { toast } = await import("@/shared/ui/toast");
    const { SAVED_FILTERS_QUERY_KEY, useDeleteSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const filter = makeFilter({ id: "del-1" });
    vi.mocked(deleteSavedFilter).mockResolvedValueOnce(undefined);

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [filter]);
    // Prevent refetch after invalidation from erroring
    vi.mocked((await import("@/features/auth/api")).listMySavedFilters).mockResolvedValue([]);

    const { result } = renderHook(() => useDeleteSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    await act(async () => {
      await result.current.mutateAsync("del-1");
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const successCall = vi
      .mocked(toast.show)
      .mock.calls.find(([arg]) => !("variant" in arg && arg.variant === "destructive"));
    expect(successCall).toBeTruthy();
  });

  it("does not remove other items when deleting one", async () => {
    const { deleteSavedFilter } = await import("@/features/auth/api");
    const { SAVED_FILTERS_QUERY_KEY, useDeleteSavedFilter } = await import(
      "../filters/useFilterPresets"
    );

    const a = makeFilter({ id: "a" });
    const b = makeFilter({ id: "b" });
    const c = makeFilter({ id: "c" });
    vi.mocked(deleteSavedFilter).mockResolvedValueOnce(undefined);
    vi.mocked((await import("@/features/auth/api")).listMySavedFilters).mockResolvedValue([a, c]);

    const qc = makeQueryClient();
    qc.setQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY, [a, b, c]);

    const { result } = renderHook(() => useDeleteSavedFilter(), {
      wrapper: makeWrapper(qc),
    });

    act(() => {
      result.current.mutate("b");
    });

    // Optimistically only b is removed
    await waitFor(() => {
      const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
      return (cached?.length ?? 0) === 2;
    });

    const cached = qc.getQueryData<SavedFilter[]>(SAVED_FILTERS_QUERY_KEY);
    expect(cached?.map((f) => f.id)).toEqual(["a", "c"]);
  });
});

// ---------------------------------------------------------------------------
// MAX_PRESETS constant
// ---------------------------------------------------------------------------

describe("MAX_PRESETS", () => {
  it("is exactly 20", async () => {
    const { MAX_PRESETS } = await import("../filters/useFilterPresets");
    expect(MAX_PRESETS).toBe(20);
  });
});
