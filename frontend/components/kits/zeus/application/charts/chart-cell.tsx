// @zeus-internal
"use client";

import type { HTMLAttributes, ReactNode } from "react";
import { Button, type ButtonProps } from "react-aria-components";
import { Tooltip, TooltipTrigger } from "../../base/tooltip/tooltip";
import { cx } from "@/lib/cx";

/** Focusable chart mark with the shared tooltip and focus treatment. */
export function ChartCell({
  tooltip,
  className,
  ...props
}: Omit<ButtonProps, "children" | "className"> & {
  tooltip: ReactNode;
  className?: string;
}) {
  return (
    <TooltipTrigger>
      <Button
        {...props}
        className={cx(
          "cursor-default outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring",
          className,
        )}
      />
      <Tooltip>{tooltip}</Tooltip>
    </TooltipTrigger>
  );
}

/** A chart swatch is descriptive by default; consumers can opt into keyboard inspection. */
export function ChartSwatch({
  focusable = false,
  onInspect,
  className,
  ...props
}: Omit<
  HTMLAttributes<HTMLElement>,
  "children" | "onFocus" | "onBlur" | "onMouseEnter" | "onMouseLeave"
> & {
  focusable?: boolean;
  onInspect?: (active: boolean) => void;
}) {
  const handlers = {
    onMouseEnter: () => onInspect?.(true),
    onMouseLeave: () => onInspect?.(false),
  };
  return focusable ? (
    <button
      {...props}
      {...handlers}
      type="button"
      onFocus={() => onInspect?.(true)}
      onBlur={() => onInspect?.(false)}
      className={cx(
        "outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring",
        className,
      )}
    />
  ) : (
    <div {...props} {...handlers} role="img" className={className} />
  );
}

/** HTML-axis labels share the same type and inspected-colour transition. */
export function ChartAxisLabel({
  active = false,
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { active?: boolean }) {
  return (
    <span
      {...props}
      className={cx(
        "text-caption-1-medium transition-colors duration-150 ease-out",
        active ? "text-text-primary" : "text-text-tertiary",
        className,
      )}
    />
  );
}
