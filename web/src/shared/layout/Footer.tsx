// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { Compass } from "lucide-react";
import * as React from "react";
import { cn } from "@/shared/lib/cn";

interface FooterProps {
  className?: string;
}

// `__APP_VERSION__` is baked into the bundle at build time from `web/package.json`
// (см. vite.config.ts `define`). Used as a fallback when the runtime fetch fails.
const BUNDLE_VERSION = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "dev";

/**
 * Runtime version source-of-truth.
 *
 * The bundle hash changes only on SPA rebuilds, so a backend-only deploy that
 * bumps `web/package.json` would otherwise leave the footer stuck on the old
 * baked version.  Fix: fetch `/version.json` (small static file served by
 * nginx alongside the bundle) on mount and use that.  Deploy ritual writes
 * `/var/www/lotsman/version.json` independently of the JS bundle, so the
 * footer always reflects what was actually deployed last.
 *
 * Cache-buster query + `cache: 'no-store'` bypass any intermediate cache.
 * On failure (dev mode, offline) we keep the bundle-baked value.
 */
async function fetchRuntimeVersion(): Promise<string | null> {
  try {
    const res = await fetch(`/version.json?_=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) return null;
    const body = (await res.json()) as { version?: unknown };
    return typeof body.version === "string" ? body.version : null;
  } catch {
    return null;
  }
}

export function Footer({ className }: FooterProps) {
  const [version, setVersion] = React.useState<string>(BUNDLE_VERSION);
  const year = new Date().getFullYear();

  React.useEffect(() => {
    let cancelled = false;
    void fetchRuntimeVersion().then((runtime) => {
      if (!cancelled && runtime) setVersion(runtime);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <footer
      role="contentinfo"
      className={cn(
        "flex h-7 shrink-0 items-center justify-between gap-3 border-t border-border bg-background/60 px-4 text-[11px] text-muted-foreground",
        className,
      )}
    >
      <span className="inline-flex items-center gap-1.5">
        <Compass className="h-3 w-3 text-primary/60" aria-hidden />
        <span>Лоцман</span>
        <span aria-hidden>·</span>
        <span className="font-mono tabular-nums">v{version}</span>
      </span>

      <a
        href="https://github.com/MaximilianKaufmannCode/lotsman"
        target="_blank"
        rel="noopener noreferrer"
        className="hidden items-center gap-1.5 rounded px-1 py-0.5 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:inline-flex"
        aria-label="Автор: Maximilian Kaufmann — исходный код на GitHub"
        title="Исходный код на GitHub"
      >
        <svg viewBox="0 0 16 16" className="h-3 w-3" fill="currentColor" aria-hidden="true">
          <title>GitHub</title>
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
        </svg>
        <span>Maximilian Kaufmann</span>
      </a>

      <span className="hidden sm:inline">© {year} · Внутренний корпоративный сервис</span>
    </footer>
  );
}
