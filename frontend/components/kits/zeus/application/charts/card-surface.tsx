import type { ComponentPropsWithRef } from "react";
import { cx } from "@/lib/cx";

/** Shared card paint and insets; each composition owns its layout and content. */
export type CardPadding = "chart" | "uniform" | "inset" | "none";
export interface CardSurfaceOptions {
  padding?: CardPadding;
  tone?: "primary" | "secondary" | "full";
}
/** Shared paint for native and animated card roots. */
export function cardSurfaceClasses({
  padding = "chart",
  tone = "secondary",
}: CardSurfaceOptions = {}) {
  return cx(
    "rounded-2xl",
    tone === "full"
      ? "bg-background-full"
      : tone === "primary"
      ? "bg-background-primary-default"
      : "bg-background-secondary-default",
    padding === "none"
      ? undefined
      : padding === "inset"
        ? "p-2.5"
        : padding === "uniform"
          ? "p-4"
          : "px-4 pt-4 pb-3",
  );
}
export function CardSurface({
  className,
  padding = "chart",
  tone = "secondary",
  ...props
}: ComponentPropsWithRef<"section"> & CardSurfaceOptions) {
  return (
    <section
      {...props}
      className={cx(cardSurfaceClasses({ padding, tone }), className)}
    />
  );
}
