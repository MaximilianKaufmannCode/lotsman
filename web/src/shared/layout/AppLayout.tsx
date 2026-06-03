// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { Outlet } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Header } from "./Header";

// NOTE: Footer is rendered ONCE at the root level (router.tsx RootShell).
// Do NOT add a Footer here — it would duplicate.

export function AppLayout() {
  const { t } = useTranslation();

  return (
    <div className="flex flex-1 flex-col min-h-0">
      {/* Skip-to-content link — a11y */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded focus:bg-primary focus:px-3 focus:py-2 focus:text-sm focus:text-primary-foreground focus:shadow"
      >
        {t("a11y.skip_to_content")}
      </a>

      <Header />

      <main id="main-content" className="flex-1 flex flex-col min-h-0 overflow-y-auto" tabIndex={-1}>
        <Outlet />
      </main>

      {/* Global live region for async status messages — WCAG 4.1.3 */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        aria-label={t("a11y.status_region")}
        className="sr-only"
        id="status-region"
      />
    </div>
  );
}
