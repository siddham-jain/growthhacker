"use client";
import type { ComponentProps } from "react";
import { cx } from "@/lib/cx";
/** Shared label paint for stand-alone indicators and conversation authors. */
export function AgentThinkingLabel({
  shimmer = true,
  variant = "conversation",
  className,
  ...props
}: ComponentProps<"span"> & { shimmer?: boolean; variant?: "conversation" | "progress" }) {
  return (
    <span
      {...props}
      className={cx(
        shimmer &&
          (variant === "progress" ? "agent-progress-loading-text" : "bui-agent-thinking-label"),
        className,
      )}
    />
  );
}
