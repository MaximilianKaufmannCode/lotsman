// SPDX-License-Identifier: BUSL-1.1
// Copyright (c) 2026 Maximilian Kaufmann. See LICENSE (Business Source License 1.1).

/**
 * Accessible tooltip using title attribute + custom visual.
 * WCAG: tooltip is supplementary — underlying element has its own accessible name.
 */
import * as React from "react";
import { cn } from "@/shared/lib/cn";

interface TooltipProps {
  content: string;
  children: React.ReactElement<React.HTMLAttributes<HTMLElement>>;
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
}

export function Tooltip({ content, children, side = "top", className }: TooltipProps) {
  const [visible, setVisible] = React.useState(false);

  const positionCls: Record<string, string> = {
    top: "bottom-full left-1/2 -translate-x-1/2 mb-1",
    bottom: "top-full left-1/2 -translate-x-1/2 mt-1",
    left: "right-full top-1/2 -translate-y-1/2 mr-1",
    right: "left-full top-1/2 -translate-y-1/2 ml-1",
  };

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: tooltip wrapper responds to mouse/focus; child element provides the interactive role
    <div
      className={cn("relative inline-flex", className)}
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
    >
      {children}
      {visible && (
        <div
          role="tooltip"
          className={cn(
            "absolute z-50 whitespace-nowrap rounded bg-foreground px-2 py-1 text-xs text-background shadow",
            positionCls[side],
          )}
        >
          {content}
        </div>
      )}
    </div>
  );
}
