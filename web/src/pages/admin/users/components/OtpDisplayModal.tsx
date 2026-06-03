// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * OtpDisplayModal — one-time display of an OTP for the show-otp delivery path.
 *
 * Security invariants (US-13):
 * - Cannot be closed by Esc or backdrop click — only the "Готово" button.
 * - OTP lives only in the caller's local state; this component receives it as a prop.
 * - No localStorage/sessionStorage retention.
 * - Each invocation is isolated: parent must reset its own state on close.
 */

import { Check, Copy } from "lucide-react";
import * as React from "react";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";

interface OtpDisplayModalProps {
  otp: string;
  ttlMinutes: number;
  onClose: () => void;
}

export function OtpDisplayModal({ otp, ttlMinutes, onClose }: OtpDisplayModalProps) {
  const [copied, setCopied] = React.useState(false);
  const panelRef = React.useRef<HTMLDivElement>(null);

  // Focus trap: on mount, focus the panel; tab stays within it
  React.useEffect(() => {
    const el = panelRef.current;
    if (!el) return;

    // Collect focusable children
    const getFocusable = () =>
      Array.from(
        el.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((n) => !n.hasAttribute("disabled"));

    const handleKeyDown = (e: KeyboardEvent) => {
      // Block Esc
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      // Tab cycle within modal
      if (e.key === "Tab") {
        const focusable = getFocusable();
        if (focusable.length === 0) {
          e.preventDefault();
          return;
        }
        const first = focusable[0] as HTMLElement;
        const last = focusable[focusable.length - 1] as HTMLElement;
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    };

    el.focus();
    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
  }, []);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(otp);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard not available in some environments — silently skip
    }
  };

  return (
    <>
      {/* Backdrop — pointer-events none so clicks pass through to the panel without closing */}
      <div
        className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        aria-hidden
        onPointerDown={(e) => e.preventDefault()}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="otp-modal-title"
        aria-describedby="otp-modal-desc"
        tabIndex={-1}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2",
          "rounded-xl border bg-card text-card-foreground shadow-xl",
          "focus:outline-none p-6 flex flex-col gap-5",
        )}
      >
        <h2 id="otp-modal-title" className="text-lg font-semibold">
          Одноразовый код для нового пользователя
        </h2>

        {/* OTP display */}
        <div className="rounded-lg bg-muted border border-border px-6 py-5 flex flex-col items-center gap-3">
          <output
            aria-label="Одноразовый код"
            className="font-mono text-3xl font-bold tracking-widest select-all"
          >
            {otp}
          </output>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleCopy}
            className="gap-2 min-w-32"
            aria-label={copied ? "Скопировано" : "Скопировать код"}
          >
            {copied ? (
              <>
                <Check className="h-4 w-4 text-status-ok" aria-hidden />
                Скопировано
              </>
            ) : (
              <>
                <Copy className="h-4 w-4" aria-hidden />
                Скопировать
              </>
            )}
          </Button>
        </div>

        <p id="otp-modal-desc" className="text-sm text-muted-foreground">
          Передайте код пользователю out-of-band (звонок, мессенджер). Действителен{" "}
          <strong>{ttlMinutes}</strong> {ttlMinutes === 1 ? "минуту" : "минут"}.
        </p>

        <Button type="button" onClick={onClose} className="w-full">
          Готово
        </Button>
      </div>
    </>
  );
}
