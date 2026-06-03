// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

"use client";
import { Moon, Sun } from "lucide-react";
import * as React from "react";
import { cn } from "@/shared/lib/cn";

type Theme = "light" | "dark";

function getSystemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getStoredTheme(): Theme | null {
  try {
    const stored = localStorage.getItem("lotsman-theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage may be unavailable (private browsing, sandboxed)
  }
  return null;
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem("lotsman-theme", theme);
  } catch {
    // ignore
  }
}

// Initialize theme before first render to avoid flash.
// Guard with typeof window to avoid running during SSR or test module init
// (window.matchMedia may not be available yet when this module is first parsed).
if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
  const initial = getStoredTheme() ?? getSystemTheme();
  applyTheme(initial);
}

interface ThemeToggleProps {
  className?: string;
}

export function ThemeToggle({ className }: ThemeToggleProps) {
  const [theme, setTheme] = React.useState<Theme>(() => getStoredTheme() ?? getSystemTheme());

  const toggle = () => {
    const next: Theme = theme === "light" ? "dark" : "light";
    setTheme(next);
    applyTheme(next);
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={theme === "light" ? "Включить тёмную тему" : "Включить светлую тему"}
      className={cn(
        "inline-flex h-9 w-9 items-center justify-center rounded-md",
        "text-muted-foreground hover:text-foreground hover:bg-accent",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "transition-colors",
        className,
      )}
    >
      {theme === "light" ? (
        <Moon className="h-4 w-4" aria-hidden />
      ) : (
        <Sun className="h-4 w-4" aria-hidden />
      )}
    </button>
  );
}
