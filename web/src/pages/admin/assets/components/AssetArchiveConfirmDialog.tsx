// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * AssetArchiveConfirmDialog — shows count of active documents that will
 * cascade-archive (per Q5 — already-archived docs are skipped).
 */

import * as React from "react";
import type { Asset } from "@/features/registry/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";

interface AssetArchiveConfirmDialogProps {
  open: boolean;
  asset: Asset | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function AssetArchiveConfirmDialog({
  open,
  asset,
  onConfirm,
  onCancel,
}: AssetArchiveConfirmDialogProps) {
  const dialogRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => dialogRef.current?.focus());
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  if (!open || !asset) return null;

  const activeDocCount = asset.document_count ?? 0;

  return (
    <>
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        aria-hidden="true"
        onClick={onCancel}
      />
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="archive-asset-title"
        aria-describedby="archive-asset-desc"
        tabIndex={-1}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-sm -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border bg-card shadow-xl focus:outline-none p-6",
        )}
      >
        <h2 id="archive-asset-title" className="text-base font-semibold">
          Архивировать компанию?
        </h2>
        <p id="archive-asset-desc" className="mt-2 text-sm text-muted-foreground">
          Компания <strong className="text-foreground">{asset.name}</strong> будет архивирована.
          {activeDocCount > 0 && (
            <>
              {" "}
              <strong className="text-destructive">{activeDocCount} активных документов</strong>{" "}
              будут архивированы вместе с ней. Уже архивированные документы не затрагиваются.
            </>
          )}
          {activeDocCount === 0 && " Документов для архивирования нет."}
        </p>
        <div className="flex gap-2 mt-4">
          <Button variant="destructive" onClick={onConfirm}>
            Архивировать
          </Button>
          <Button variant="outline" onClick={onCancel}>
            Отмена
          </Button>
        </div>
      </div>
    </>
  );
}
