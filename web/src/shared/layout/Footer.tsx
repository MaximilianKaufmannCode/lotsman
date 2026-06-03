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
      <span className="hidden sm:inline">© {year} · Внутренний корпоративный сервис</span>
    </footer>
  );
}
