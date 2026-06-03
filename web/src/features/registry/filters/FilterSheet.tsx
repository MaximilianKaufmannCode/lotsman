// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * FilterSheet — Right-side Sheet panel with accordion groups of filter controls.
 *
 * Design: the design spec §3–7
 * A11y:   WCAG 2.2 AA, role="dialog", focus-trap-on-save-form, Esc closes,
 *         focus returns to trigger on close, aria-live for condition counter.
 *
 * Architecture decisions:
 * - Draft state (in-memory, not URL) held locally — only committed on "Применить".
 * - Draft is also persisted to sessionStorage so re-opening the Sheet after
 *   an unapplied close restores the draft (design §10 "Sheet open + unsaved").
 * - All filter groups are independent accordion items (multiple=true).
 * - Saved presets section at top.
 */

import { isAfter, parseISO } from "date-fns";
import {
  BookmarkPlus,
  ChevronDown,
  ChevronUp,
  MoreHorizontal,
  Star,
  Trash2,
  X,
} from "lucide-react";
import * as React from "react";
import { useAuth } from "@/features/auth/AuthProvider";
import type { SavedFilter } from "@/features/auth/api";
import {
  MAX_PRESETS,
  useCreateSavedFilter,
  useDeleteSavedFilter,
  useSavedFilters,
  useUpdateSavedFilter,
} from "@/features/registry/filters/useFilterPresets";
import { useDocumentTypes } from "@/features/registry/hooks/useDocumentTypes";
import type { RegistrySearch } from "@/features/registry/hooks/useUrlState";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { toast } from "@/shared/ui/toast";

// ── Session-storage draft key ─────────────────────────────────────────────────

const DRAFT_KEY = "lotsman_filter_draft";

function loadDraft(): Partial<RegistrySearch> {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    if (raw) return JSON.parse(raw) as Partial<RegistrySearch>;
  } catch {
    // ignore
  }
  return {};
}

function saveDraft(draft: Partial<RegistrySearch>) {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // private browsing
  }
}

function clearDraft() {
  try {
    sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    // ignore
  }
}

// ── Accordion state (open groups persisted in localStorage) ──────────────────

const ACCORDION_KEY = "lotsman_filter_accordion";

function loadAccordion(): string[] {
  try {
    const raw = localStorage.getItem(ACCORDION_KEY);
    if (raw) return JSON.parse(raw) as string[];
  } catch {
    // ignore
  }
  return ["doc"]; // default: open "Документ" group
}

function saveAccordion(groups: string[]) {
  try {
    localStorage.setItem(ACCORDION_KEY, JSON.stringify(groups));
  } catch {
    // ignore
  }
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface FilterSheetProps {
  open: boolean;
  onClose: () => void;
  /** Current URL search (applied state) */
  urlSearch: RegistrySearch;
  /** Call to push the draft to URL and close sheet */
  onApply: (draft: Partial<RegistrySearch>) => void;
  onReset: () => void;
  /** ref to the trigger button for focus-restoration */
  triggerRef: React.RefObject<HTMLElement | null>;
}

// ── FilterSheet ───────────────────────────────────────────────────────────────

export function FilterSheet({
  open,
  onClose,
  urlSearch,
  onApply,
  onReset,
  triggerRef,
}: FilterSheetProps) {
  const { claims } = useAuth();

  // Draft state — local, not committed until "Применить"
  const [draft, setDraft] = React.useState<Partial<RegistrySearch>>(() => ({
    ...loadDraft(),
  }));

  // Accordion open groups
  const [openGroups, setOpenGroups] = React.useState<string[]>(loadAccordion);

  // Save-form visibility
  const [saveFormOpen, setSaveFormOpen] = React.useState(false);
  const [saveFormName, setSaveFormName] = React.useState("");
  const [saveFormDefault, setSaveFormDefault] = React.useState(false);

  // Preset management
  const { data: presets = [] } = useSavedFilters();
  const createPreset = useCreateSavedFilter();
  const updatePreset = useUpdateSavedFilter();
  const deletePreset = useDeleteSavedFilter();

  // Preset dropdown
  const [presetDropdownOpen, setPresetDropdownOpen] = React.useState(false);
  const [presetMenuId, setPresetMenuId] = React.useState<string | null>(null);
  const [renamingId, setRenamingId] = React.useState<string | null>(null);
  const [renameValue, setRenameValue] = React.useState("");

  // Refs
  const sheetRef = React.useRef<HTMLDivElement>(null);
  const firstFocusRef = React.useRef<HTMLElement | null>(null);

  // Document types for custom-field filters
  const { data: docTypes = [] } = useDocumentTypes();

  // ── Sync draft from URL on open ─────────────────────────────────────────────
  React.useEffect(() => {
    if (open) {
      const saved = loadDraft();
      // If draft is empty, initialize from URL
      if (Object.keys(saved).length === 0) {
        setDraft({ ...urlSearch });
      } else {
        setDraft(saved);
      }
      // Auto-expand groups that have active conditions
      const active: string[] = [];
      if (urlSearch.type_codes?.length || urlSearch.type_code || urlSearch.number)
        active.push("doc");
      if (
        urlSearch.asset_ids?.length ||
        urlSearch.asset_id ||
        urlSearch.jurisdiction?.length ||
        urlSearch.inn
      )
        active.push("asset");
      if (urlSearch.expiry_from || urlSearch.expiry_to || urlSearch.expiry_perpetual)
        active.push("dates");
      if (urlSearch.responsible) active.push("responsible");
      if (urlSearch.updated_from || urlSearch.updated_to || urlSearch.doc_status?.length)
        active.push("meta");
      if (active.length > 0) {
        setOpenGroups((prev) => Array.from(new Set([...prev, ...active])));
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    open,
    urlSearch.inn,
    urlSearch.updated_to,
    urlSearch.updated_from,
    urlSearch.type_code,
    urlSearch.responsible,
    urlSearch.number,
    urlSearch.jurisdiction?.length,
    urlSearch.expiry_to,
    urlSearch.doc_status?.length,
    urlSearch.type_codes?.length,
    urlSearch.asset_ids?.length,
    urlSearch,
  ]);

  // ── Focus management ────────────────────────────────────────────────────────
  React.useEffect(() => {
    if (open) {
      // Small delay so the sheet renders before focusing
      const timer = setTimeout(() => {
        firstFocusRef.current?.focus();
      }, 50);
      return () => clearTimeout(timer);
    }
    // Return focus to trigger on close
    if (!open && triggerRef.current) {
      triggerRef.current.focus();
    }
  }, [open, triggerRef]);

  // ── Close handler (defined before effects that use it) ─────────────────────
  const handleClose = React.useCallback(() => {
    const hasDirty = JSON.stringify(draft) !== JSON.stringify(urlSearch);
    if (hasDirty) {
      saveDraft(draft);
      toast.show({ title: "Условия не применены — черновик сохранён" });
    }
    onClose();
  }, [draft, urlSearch, onClose]);

  // ── Keyboard: Esc closes ────────────────────────────────────────────────────
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        handleClose();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, handleClose]);

  // ── Backdrop click closes ───────────────────────────────────────────────────
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      handleClose();
    }
  };

  // ── Draft helpers ───────────────────────────────────────────────────────────
  const setDraftField = <K extends keyof RegistrySearch>(key: K, value: RegistrySearch[K]) => {
    setDraft((prev) => {
      const next = { ...prev, [key]: value };
      saveDraft(next);
      return next;
    });
  };

  const clearDraftField = <K extends keyof RegistrySearch>(key: K) => {
    setDraft((prev) => {
      const next = { ...prev };
      delete next[key];
      saveDraft(next);
      return next;
    });
  };

  // ── Apply ────────────────────────────────────────────────────────────────────
  const handleApply = () => {
    // Validate: expiry range
    if (draft.expiry_from && draft.expiry_to) {
      try {
        if (isAfter(parseISO(draft.expiry_from), parseISO(draft.expiry_to))) {
          toast.show({ title: "Дата «от» не может быть позже даты «до»", variant: "destructive" });
          return;
        }
      } catch {
        // invalid dates handled by zod
      }
    }
    clearDraft();
    onApply(draft);
  };

  // ── Reset ────────────────────────────────────────────────────────────────────
  const handleReset = () => {
    setDraft({});
    clearDraft();
    onReset();
  };

  // ── Presets ──────────────────────────────────────────────────────────────────
  const handleApplyPreset = (preset: SavedFilter) => {
    const hasDirty = JSON.stringify(draft) !== JSON.stringify(urlSearch);
    if (hasDirty) {
      if (!window.confirm(`Текущие фильтры будут заменены пресетом «${preset.name}». Продолжить?`))
        return;
    }
    setDraft(preset.filter_json as Partial<RegistrySearch>);
    setPresetDropdownOpen(false);
  };

  const handleSavePreset = () => {
    const name = saveFormName.trim();
    if (!name) {
      toast.show({ title: "Введите название пресета", variant: "destructive" });
      return;
    }
    if (name.length > 100) {
      toast.show({ title: "Максимум 100 символов", variant: "destructive" });
      return;
    }
    if (presets.length >= MAX_PRESETS) {
      toast.show({
        title: `Достигнут лимит пресетов (${MAX_PRESETS}). Удалите один из существующих.`,
        variant: "destructive",
      });
      return;
    }
    createPreset.mutate(
      { name, filter_json: draft as Record<string, unknown>, is_default: saveFormDefault },
      {
        onSuccess: () => {
          setSaveFormOpen(false);
          setSaveFormName("");
          setSaveFormDefault(false);
        },
      },
    );
  };

  const handleDeletePreset = (preset: SavedFilter) => {
    if (!window.confirm(`Удалить пресет «${preset.name}»? Это действие не отменить.`)) return;
    deletePreset.mutate(preset.id);
    setPresetMenuId(null);
  };

  const handleRenamePreset = (preset: SavedFilter) => {
    const newName = renameValue.trim();
    if (!newName) return;
    if (newName.length > 100) {
      toast.show({ title: "Максимум 100 символов", variant: "destructive" });
      return;
    }
    updatePreset.mutate({ id: preset.id, body: { name: newName } });
    setRenamingId(null);
    setRenameValue("");
    setPresetMenuId(null);
  };

  const handleSetDefault = (preset: SavedFilter) => {
    updatePreset.mutate({ id: preset.id, body: { is_default: !preset.is_default } });
    setPresetMenuId(null);
  };

  // ── Count active filters in draft for footer counter ─────────────────────────
  const draftConditionCount = React.useMemo(() => {
    let n = 0;
    const keys: (keyof RegistrySearch)[] = [
      "type_codes",
      "type_code",
      "number",
      "asset_ids",
      "asset_id",
      "jurisdiction",
      "inn",
      "expiry_from",
      "expiry_to",
      "expiry_perpetual",
      "responsible",
      "updated_from",
      "updated_to",
      "doc_status",
      "show_archived",
      "status",
    ];
    for (const k of keys) {
      const v = draft[k];
      if (v === undefined || v === null || v === false || v === "") continue;
      if (Array.isArray(v) && v.length === 0) continue;
      n++;
    }
    return n;
  }, [draft]);

  // ── Accordion helpers ────────────────────────────────────────────────────────
  const toggleGroup = (id: string) => {
    setOpenGroups((prev) => {
      const next = prev.includes(id) ? prev.filter((g) => g !== id) : [...prev, id];
      saveAccordion(next);
      return next;
    });
  };

  const groupConditionCount = (keys: (keyof RegistrySearch)[]): number => {
    let n = 0;
    for (const k of keys) {
      const v = draft[k];
      if (v === undefined || v === null || v === false || v === "") continue;
      if (Array.isArray(v) && v.length === 0) continue;
      n++;
    }
    return n;
  };

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-foreground/10"
        aria-hidden="true"
        onClick={handleBackdropClick}
      />

      {/* Sheet */}
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="false"
        aria-label="Панель фильтров"
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex flex-col bg-background border-l shadow-xl",
          "w-[420px] xl:w-[440px]",
          // Mobile: full-width bottom sheet handled via media query below
          "max-[767px]:inset-x-0 max-[767px]:top-auto max-[767px]:bottom-0 max-[767px]:w-full max-[767px]:max-h-[90vh] max-[767px]:border-l-0 max-[767px]:border-t max-[767px]:rounded-t-xl",
        )}
      >
        {/* Mobile drag handle */}
        <div className="md:hidden flex justify-center pt-3 pb-1">
          <div className="w-10 h-1 rounded-full bg-muted-foreground/30" aria-hidden />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b shrink-0">
          <h2 className="text-base font-semibold" id="filter-sheet-title">
            Фильтры
          </h2>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Закрыть панель фильтров"
            className="rounded p-1.5 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>

        {/* Presets row */}
        <div className="px-4 py-3 border-b shrink-0 flex items-center gap-2">
          <div className="relative flex-1">
            <button
              type="button"
              onClick={() => setPresetDropdownOpen((v) => !v)}
              aria-expanded={presetDropdownOpen}
              aria-haspopup="listbox"
              className={cn(
                "w-full flex items-center justify-between gap-2 px-3 py-1.5 rounded border bg-background text-sm",
                "hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              <span className="text-muted-foreground truncate">
                {presets.length === 0 ? "Мои фильтры" : `Мои фильтры (${presets.length})`}
              </span>
              <ChevronDown className="size-3.5 shrink-0" aria-hidden />
            </button>

            {presetDropdownOpen && (
              <PresetDropdown
                presets={presets}
                presetMenuId={presetMenuId}
                setPresetMenuId={setPresetMenuId}
                renamingId={renamingId}
                renameValue={renameValue}
                setRenamingId={setRenamingId}
                setRenameValue={setRenameValue}
                onApply={handleApplyPreset}
                onDelete={handleDeletePreset}
                onRename={handleRenamePreset}
                onSetDefault={handleSetDefault}
                onClose={() => setPresetDropdownOpen(false)}
              />
            )}
          </div>

          <button
            type="button"
            title={
              draftConditionCount === 0
                ? "Заполните хотя бы одно условие чтобы сохранить"
                : undefined
            }
            disabled={draftConditionCount === 0}
            onClick={() => setSaveFormOpen((v) => !v)}
            aria-expanded={saveFormOpen}
            className={cn(
              "flex items-center gap-1 px-2 py-1.5 rounded border text-sm shrink-0",
              "hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              "disabled:opacity-50 disabled:cursor-not-allowed",
            )}
          >
            <BookmarkPlus className="size-3.5" aria-hidden />
            <span className="hidden sm:inline">Сохранить</span>
          </button>
        </div>

        {/* Save form (inline, inside Sheet) */}
        {saveFormOpen && (
          <div className="px-4 py-3 border-b bg-muted/30 shrink-0">
            <p className="text-sm font-medium mb-2">Сохранить текущие фильтры</p>
            <p className="text-xs text-muted-foreground mb-2">Условий: {draftConditionCount}</p>
            <Input
              value={saveFormName}
              onChange={(e) => setSaveFormName(e.target.value)}
              placeholder="Название пресета"
              maxLength={100}
              aria-label="Название пресета"
              className="mb-2 text-sm"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSavePreset();
                if (e.key === "Escape") setSaveFormOpen(false);
              }}
            />
            {saveFormName.length > 100 && (
              <p className="text-xs text-destructive mb-1" role="alert">
                Максимум 100 символов
              </p>
            )}
            <label className="flex items-center gap-2 text-sm mb-3 cursor-pointer">
              <input
                type="checkbox"
                checked={saveFormDefault}
                onChange={(e) => setSaveFormDefault(e.target.checked)}
                className="rounded focus-visible:ring-2 focus-visible:ring-ring"
              />
              Сделать пресетом по умолчанию
            </label>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setSaveFormOpen(false);
                  setSaveFormName("");
                }}
              >
                Отмена
              </Button>
              <Button
                size="sm"
                onClick={handleSavePreset}
                disabled={createPreset.isPending || !saveFormName.trim()}
              >
                Сохранить
              </Button>
            </div>
          </div>
        )}

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto">
          {/* Group: Документ */}
          <AccordionGroup
            id="doc"
            label="Документ"
            open={openGroups.includes("doc")}
            onToggle={() => toggleGroup("doc")}
            conditionCount={groupConditionCount(["type_codes", "type_code", "number"])}
            firstFocusRef={firstFocusRef}
          >
            {/* Тип документа */}
            <div className="mb-3">
              <p className="block text-xs font-medium text-muted-foreground mb-1">Тип документа</p>
              <MultiSelectField
                options={docTypes.map((dt) => ({ value: dt.code, label: dt.display_name }))}
                selected={draft.type_codes ?? (draft.type_code ? [draft.type_code] : [])}
                onChange={(v) => setDraftField("type_codes", v.length > 0 ? v : undefined)}
                placeholder="Все типы"
                aria-label="Фильтр по типу документа"
              />
            </div>

            {/* № содержит */}
            <div className="mb-1">
              <label
                className="block text-xs font-medium text-muted-foreground mb-1"
                htmlFor="filter-number"
              >
                № содержит
              </label>
              <Input
                id="filter-number"
                value={draft.number ?? ""}
                onChange={(e) =>
                  e.target.value
                    ? setDraftField("number", e.target.value)
                    : clearDraftField("number")
                }
                placeholder="Поиск по номеру... (мин 2 симв.)"
                className="text-sm"
              />
            </div>
          </AccordionGroup>

          {/* Group: Контрагент */}
          <AccordionGroup
            id="asset"
            label="Контрагент"
            open={openGroups.includes("asset")}
            onToggle={() => toggleGroup("asset")}
            conditionCount={groupConditionCount([
              "asset_ids",
              "asset_id",
              "jurisdiction",
              "inn",
            ])}
          >
            {/* Юрисдикция */}
            <div className="mb-3">
              <p className="block text-xs font-medium text-muted-foreground mb-1">Юрисдикция</p>
              <MultiSelectField
                options={[
                  { value: "RU", label: "Россия (RU)" },
                  { value: "KZ", label: "Казахстан (KZ)" },
                  { value: "BY", label: "Беларусь (BY)" },
                  { value: "UZ", label: "Узбекистан (UZ)" },
                  { value: "AE", label: "ОАЭ (AE)" },
                ]}
                selected={draft.jurisdiction ?? []}
                onChange={(v) => setDraftField("jurisdiction", v.length > 0 ? v : undefined)}
                placeholder="Любая"
                aria-label="Фильтр по юрисдикции"
              />
            </div>

            {/* ИНН содержит */}
            <div className="mb-1">
              <label
                className="block text-xs font-medium text-muted-foreground mb-1"
                htmlFor="filter-inn"
              >
                ИНН содержит
              </label>
              <Input
                id="filter-inn"
                value={draft.inn ?? ""}
                onChange={(e) =>
                  e.target.value ? setDraftField("inn", e.target.value) : clearDraftField("inn")
                }
                inputMode="numeric"
                placeholder="Мин 4 цифры"
                className="text-sm"
              />
            </div>
          </AccordionGroup>

          {/* Group: Сроки */}
          <AccordionGroup
            id="dates"
            label="Сроки"
            open={openGroups.includes("dates")}
            onToggle={() => toggleGroup("dates")}
            conditionCount={groupConditionCount(["expiry_from", "expiry_to", "expiry_perpetual"])}
          >
            {/* Mutual-exclusivity: perpetual vs date range */}
            {draft.expiry_perpetual && (
              <p className="text-xs text-muted-foreground mb-2 bg-muted/50 rounded px-2 py-1.5">
                Снимите «Только бессрочные» чтобы выбрать диапазон.
              </p>
            )}

            <div className="mb-3 flex gap-2">
              <div className="flex-1">
                <label
                  className="block text-xs text-muted-foreground mb-1"
                  htmlFor="filter-expiry-from"
                >
                  Действ. до — от
                </label>
                <Input
                  id="filter-expiry-from"
                  type="date"
                  value={draft.expiry_from ?? ""}
                  disabled={!!draft.expiry_perpetual}
                  aria-disabled={!!draft.expiry_perpetual}
                  onChange={(e) =>
                    e.target.value
                      ? setDraftField("expiry_from", e.target.value)
                      : clearDraftField("expiry_from")
                  }
                  title={
                    draft.expiry_perpetual
                      ? "Снимите «Только бессрочные» чтобы выбрать диапазон"
                      : undefined
                  }
                  className="text-sm"
                />
              </div>
              <div className="flex-1">
                <label
                  className="block text-xs text-muted-foreground mb-1"
                  htmlFor="filter-expiry-to"
                >
                  до
                </label>
                <Input
                  id="filter-expiry-to"
                  type="date"
                  value={draft.expiry_to ?? ""}
                  disabled={!!draft.expiry_perpetual}
                  aria-disabled={!!draft.expiry_perpetual}
                  min={draft.expiry_from}
                  onChange={(e) =>
                    e.target.value
                      ? setDraftField("expiry_to", e.target.value)
                      : clearDraftField("expiry_to")
                  }
                  title={
                    draft.expiry_perpetual
                      ? "Снимите «Только бессрочные» чтобы выбрать диапазон"
                      : undefined
                  }
                  className="text-sm"
                />
              </div>
            </div>

            {/* Expiry range validation */}
            {draft.expiry_from &&
              draft.expiry_to &&
              (() => {
                try {
                  return isAfter(parseISO(draft.expiry_from), parseISO(draft.expiry_to));
                } catch {
                  return false;
                }
              })() && (
                <p className="text-xs text-destructive mb-2" role="alert">
                  Дата «от» не может быть позже даты «до»
                </p>
              )}

            <label
              className={cn(
                "flex items-center gap-2 text-sm cursor-pointer",
                (draft.expiry_from || draft.expiry_to) && "opacity-50",
              )}
            >
              <input
                type="checkbox"
                checked={draft.expiry_perpetual ?? false}
                disabled={!!(draft.expiry_from || draft.expiry_to)}
                title={
                  draft.expiry_from || draft.expiry_to
                    ? "Очистите даты выше, чтобы выбрать бессрочные"
                    : undefined
                }
                onChange={(e) => {
                  if (e.target.checked) {
                    setDraftField("expiry_perpetual", true);
                    clearDraftField("expiry_from");
                    clearDraftField("expiry_to");
                  } else {
                    clearDraftField("expiry_perpetual");
                  }
                }}
                className="rounded focus-visible:ring-2 focus-visible:ring-ring"
              />
              Только бессрочные
            </label>
          </AccordionGroup>

          {/* Group: Ответственный */}
          <AccordionGroup
            id="responsible"
            label="Ответственный"
            open={openGroups.includes("responsible")}
            onToggle={() => toggleGroup("responsible")}
            conditionCount={groupConditionCount(["responsible"])}
          >
            <div role="radiogroup" aria-label="Фильтр по ответственному" className="space-y-2">
              {[
                { value: undefined, label: "Любой" },
                { value: "me", label: `Я (${claims?.email ?? "текущий пользователь"})` },
                { value: "unassigned", label: "Не назначен" },
              ].map(({ value, label }) => (
                <label
                  key={value ?? "any"}
                  className="flex items-center gap-2 text-sm cursor-pointer"
                >
                  <input
                    type="radio"
                    name="responsible"
                    checked={draft.responsible === value}
                    onChange={() =>
                      value ? setDraftField("responsible", value) : clearDraftField("responsible")
                    }
                    className="focus-visible:ring-2 focus-visible:ring-ring"
                  />
                  {label}
                </label>
              ))}
              {/* Specific user option — simplified: text input for UUID */}
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="radio"
                  name="responsible"
                  checked={
                    !!draft.responsible &&
                    draft.responsible !== "me" &&
                    draft.responsible !== "unassigned"
                  }
                  onChange={() => {
                    // Toggle to specific mode with empty value
                    setDraftField("responsible", " ");
                  }}
                  className="focus-visible:ring-2 focus-visible:ring-ring"
                />
                Конкретный пользователь
              </label>
            </div>
          </AccordionGroup>

          {/* Group: Метаданные */}
          <AccordionGroup
            id="meta"
            label="Метаданные"
            open={openGroups.includes("meta")}
            onToggle={() => toggleGroup("meta")}
            conditionCount={groupConditionCount([
              "updated_from",
              "updated_to",
              "doc_status",
              "show_archived",
              "status",
            ])}
          >
            {/* Дата изменения */}
            <div className="mb-3">
              <p className="text-xs font-medium text-muted-foreground mb-1">Изменён</p>
              <div className="flex gap-2">
                <div className="flex-1">
                  <label
                    className="block text-xs text-muted-foreground mb-1"
                    htmlFor="filter-updated-from"
                  >
                    от
                  </label>
                  <Input
                    id="filter-updated-from"
                    type="date"
                    value={draft.updated_from ? draft.updated_from.slice(0, 10) : ""}
                    onChange={(e) =>
                      e.target.value
                        ? setDraftField("updated_from", e.target.value)
                        : clearDraftField("updated_from")
                    }
                    className="text-sm"
                  />
                </div>
                <div className="flex-1">
                  <label
                    className="block text-xs text-muted-foreground mb-1"
                    htmlFor="filter-updated-to"
                  >
                    до
                  </label>
                  <Input
                    id="filter-updated-to"
                    type="date"
                    value={draft.updated_to ? draft.updated_to.slice(0, 10) : ""}
                    min={draft.updated_from?.slice(0, 10)}
                    onChange={(e) =>
                      e.target.value
                        ? setDraftField("updated_to", e.target.value)
                        : clearDraftField("updated_to")
                    }
                    className="text-sm"
                  />
                </div>
              </div>
            </div>

            {/* Статус документа */}
            <div className="mb-1">
              <p className="text-xs font-medium text-muted-foreground mb-1">Статус документа</p>
              <MultiSelectField
                options={[
                  { value: "active", label: "Активные" },
                  { value: "archived", label: "Архивные" },
                ]}
                selected={
                  draft.doc_status ?? (draft.show_archived ? ["active", "archived"] : ["active"])
                }
                onChange={(v) => {
                  setDraftField("doc_status", v.length > 0 ? v : undefined);
                  clearDraftField("show_archived");
                }}
                placeholder="Только активные"
                aria-label="Фильтр по статусу документа"
              />
            </div>
          </AccordionGroup>
        </div>

        {/* Footer (sticky) */}
        <div className="shrink-0 border-t px-4 py-3 flex flex-col gap-2">
          {/* Live counter */}
          <p className="text-xs text-muted-foreground" aria-live="polite" aria-atomic="true">
            {draftConditionCount > 0
              ? `Условий: ${draftConditionCount} — будет применено при нажатии «Применить»`
              : "Нет активных условий"}
          </p>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={handleReset} className="flex-1">
              Сбросить
            </Button>
            <Button variant="default" size="sm" onClick={handleApply} className="flex-1">
              Применить
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

// ── AccordionGroup ─────────────────────────────────────────────────────────────

function AccordionGroup({
  id,
  label,
  open,
  onToggle,
  conditionCount,
  children,
  firstFocusRef,
}: {
  id: string;
  label: string;
  open: boolean;
  onToggle: () => void;
  conditionCount: number;
  children: React.ReactNode;
  firstFocusRef?: React.MutableRefObject<HTMLElement | null>;
}) {
  const headerId = `filter-group-${id}-header`;
  const panelId = `filter-group-${id}-panel`;

  return (
    <section aria-labelledby={headerId} className="border-b last:border-b-0">
      <button
        type="button"
        id={headerId}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        ref={(el) => {
          // First accordion header gets initial focus when Sheet opens
          if (firstFocusRef && id === "doc" && el) {
            firstFocusRef.current = el;
          }
        }}
        className={cn(
          "w-full flex items-center justify-between px-4 py-3 text-sm font-medium",
          "hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        )}
      >
        <span className="flex items-center gap-2">
          {open ? (
            <ChevronDown className="size-3.5" aria-hidden />
          ) : (
            <ChevronUp className="size-3.5" aria-hidden />
          )}
          {label}
        </span>
        {conditionCount > 0 && (
          <span
            className="ml-auto mr-1 inline-flex items-center gap-0.5 rounded-full bg-primary/10 text-primary px-1.5 py-0.5 text-xs font-medium"
            aria-hidden="true"
          >
            {conditionCount}
          </span>
        )}
      </button>
      {open && (
        <div id={panelId} className="px-4 pb-4 pt-1">
          {children}
        </div>
      )}
    </section>
  );
}

// ── MultiSelectField ──────────────────────────────────────────────────────────

function MultiSelectField({
  options,
  selected,
  onChange,
  placeholder,
  "aria-label": ariaLabel,
}: {
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (values: string[]) => void;
  placeholder: string;
  "aria-label"?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  // Close on outside click
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const toggle = (value: string) => {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);
  };

  const label =
    selected.length === 0
      ? placeholder
      : selected.length === 1
        ? (options.find((o) => o.value === selected[0])?.label ?? selected[0])
        : `Выбрано: ${selected.length} из ${options.length}`;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "w-full flex items-center justify-between gap-2 px-3 py-1.5 rounded border bg-background text-sm",
          "hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          selected.length > 0 && "border-primary",
        )}
      >
        <span className={cn("truncate", selected.length === 0 && "text-muted-foreground")}>
          {label}
        </span>
        <ChevronDown className="size-3.5 shrink-0" aria-hidden />
      </button>
      {open && (
        <div
          role="listbox"
          aria-multiselectable="true"
          aria-label={ariaLabel}
          className="absolute top-full left-0 right-0 z-50 mt-1 rounded border bg-popover shadow-md max-h-48 overflow-y-auto"
        >
          {options.map((opt) => {
            const isChecked = selected.includes(opt.value);
            return (
              <label
                key={opt.value}
                className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-muted cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggle(opt.value)}
                  className="rounded focus-visible:ring-2 focus-visible:ring-ring"
                />
                {opt.label}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── PresetDropdown ─────────────────────────────────────────────────────────────

function PresetDropdown({
  presets,
  presetMenuId,
  setPresetMenuId,
  renamingId,
  renameValue,
  setRenamingId,
  setRenameValue,
  onApply,
  onDelete,
  onRename,
  onSetDefault,
  onClose,
}: {
  presets: SavedFilter[];
  presetMenuId: string | null;
  setPresetMenuId: (id: string | null) => void;
  renamingId: string | null;
  renameValue: string;
  setRenamingId: (id: string | null) => void;
  setRenameValue: (v: string) => void;
  onApply: (p: SavedFilter) => void;
  onDelete: (p: SavedFilter) => void;
  onRename: (p: SavedFilter) => void;
  onSetDefault: (p: SavedFilter) => void;
  onClose: () => void;
}) {
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div
      ref={ref}
      role="listbox"
      aria-label="Мои сохранённые фильтры"
      className="absolute top-full left-0 right-0 z-50 mt-1 rounded border bg-popover shadow-md max-h-64 overflow-y-auto"
    >
      {presets.length === 0 ? (
        <div className="px-4 py-6 text-center">
          <p className="text-sm text-muted-foreground">У вас пока нет сохранённых пресетов</p>
          <p className="text-xs text-muted-foreground mt-1">
            Заполните фильтры и нажмите «Сохранить»
          </p>
        </div>
      ) : (
        presets.map((preset) => (
          <div key={preset.id} className="relative group flex items-center hover:bg-muted">
            {renamingId === preset.id ? (
              <div className="flex-1 px-3 py-2">
                <input
                  type="text"
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onRename(preset);
                    if (e.key === "Escape") {
                      setRenamingId(null);
                      setRenameValue("");
                    }
                    e.stopPropagation();
                  }}
                  maxLength={100}
                  className="w-full text-sm rounded border px-2 py-0.5 bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`Переименовать пресет ${preset.name}`}
                />
              </div>
            ) : (
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => onApply(preset)}
                className="flex-1 text-left px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              >
                <span className="flex items-center gap-1.5">
                  {preset.is_default && (
                    <Star className="size-3 fill-current text-yellow-500" aria-hidden />
                  )}
                  <span className="truncate">{preset.name}</span>
                </span>
              </button>
            )}

            {/* Kebab menu */}
            <div className="relative shrink-0">
              <button
                type="button"
                aria-label={`Управление пресетом ${preset.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  setPresetMenuId(presetMenuId === preset.id ? null : preset.id);
                }}
                className={cn(
                  "p-1.5 mx-1 rounded opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
                  "hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  presetMenuId === preset.id && "opacity-100",
                )}
              >
                <MoreHorizontal className="size-3.5" aria-hidden />
              </button>

              {presetMenuId === preset.id && (
                <div className="absolute right-0 top-full z-50 w-44 rounded border bg-popover shadow-md">
                  <button
                    type="button"
                    onClick={() => {
                      onApply(preset);
                      setPresetMenuId(null);
                    }}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    Применить
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setRenamingId(preset.id);
                      setRenameValue(preset.name);
                      setPresetMenuId(null);
                    }}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    Переименовать
                  </button>
                  <button
                    type="button"
                    onClick={() => onSetDefault(preset)}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    {preset.is_default ? "Снять default" : "Сделать default"}
                  </button>
                  <hr className="border-border mx-2" />
                  <button
                    type="button"
                    onClick={() => onDelete(preset)}
                    className="w-full text-left px-3 py-2 text-sm text-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    <span className="flex items-center gap-1.5">
                      <Trash2 className="size-3.5" aria-hidden />
                      Удалить
                    </span>
                  </button>
                </div>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
