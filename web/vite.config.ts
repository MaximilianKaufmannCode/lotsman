// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve as resolvePath } from "node:path";
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type ViteDevServer } from "vite";

const pkgPath = fileURLToPath(new URL("./package.json", import.meta.url));
const pkg = JSON.parse(readFileSync(pkgPath, "utf-8")) as { version: string };

/**
 * Restart-on-package.json-change plugin.
 *
 * `define: __APP_VERSION__` is a compile-time constant baked at server start.
 * If package.json bumps after dev server is already running, the served
 * bundle keeps the OLD version. This plugin watches package.json and forces
 * a full server restart when it changes — so the new version is picked up
 * automatically (no more "kill+restart vite manually" after each bump).
 */
/**
 * Emit `dist/version.json` after each production build.
 *
 * The runtime footer fetches `/version.json` to display the deployed version
 * (see `web/src/shared/layout/Footer.tsx`). Building it as part of `vite build`
 * guarantees the file is always co-shipped with the JS bundle when SPA is
 * rebuilt. For backend-only deploys, the deploy script also writes this file
 * directly to `/var/www/lotsman/version.json` so a SPA rebuild is not required
 * just to bump the displayed version number.
 */
function emitVersionJson() {
  return {
    name: "lotsman-emit-version-json",
    apply: "build" as const,
    closeBundle() {
      const outDir = resolvePath(fileURLToPath(new URL("./dist", import.meta.url)));
      mkdirSync(dirname(`${outDir}/version.json`), { recursive: true });
      writeFileSync(`${outDir}/version.json`, JSON.stringify({ version: pkg.version }) + "\n");
      // eslint-disable-next-line no-console
      console.log(`[lotsman] emitted dist/version.json = ${pkg.version}`);
    },
  };
}

function restartOnPackageJsonChange() {
  return {
    name: "restart-on-pkg-version-change",
    configureServer(server: ViteDevServer) {
      server.watcher.add(pkgPath);
      server.watcher.on("change", (file: string) => {
        if (file === pkgPath) {
          // eslint-disable-next-line no-console
          console.log(
            "\n[lotsman] package.json changed → restarting vite to refresh __APP_VERSION__",
          );
          server.restart();
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), restartOnPackageJsonChange(), emitVersionJson()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
