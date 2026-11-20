"use client";

import type { ReactNode } from "react";
import { useCallback, useMemo, useState } from "react";

import { Chip } from "../../base/badges/chip";
import { useCountUp } from "@/hooks/use-count-up";
import { cx } from "@/lib/cx";
import { CardSurface } from "./card-surface";

/**
 * The frame every chart card in the system shares.
 *
 * The cards differ only in the mark they draw, so everything around the mark
 * lives here once: the 344px surface, the header that swaps its headline for
 * whatever the pointer is over, the count-up figure, the delta chip and the
 * legend. A new chart is then a component that renders a Recharts (or SVG)
 * mark and hands this its numbers — which is why the family stays consistent
 * as it grows.
 */

export type DeltaColor = "lime" | "rose" | "neutral";

export interface ChartDelta {
  label: string;
  color: DeltaColor;
}

/** Percentage change from `previous` to `current`, as a chip-ready label. */
export function describeDelta(current: number, previous: number): ChartDelta {
  if (previous === 0) return { label: "New", color: "neutral" };
  const change = ((current - previous) / previous) * 100;
  const rounded = Math.round(change * 10) / 10;
  if (rounded === 0) return { label: "0%", color: "neutral" };
  return {
    label: `${rounded > 0 ? "+" : ""}${rounded}%`,
    color: rounded > 0 ? "lime" : "rose",
  };
}

/** Compact axis labels: 1200 → 1.2k, 1_400_000 → 1.4M. */
export function formatCompact(value: number, prefix = ""): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${prefix}${Math.round(value / 100_000) / 10}M`;
  if (abs >= 1_000) return `${prefix}${Math.round(value / 100) / 10}k`;
  return `${prefix}${value}`;
}

/** Thousands-separated, no decimals — the headline format. */
export function formatNumber(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

/**
 * What Recharts hands its chart-level mouse handlers.
 *
 * Kept structural rather than imported: Recharts types `activeTooltipIndex` as
 * `number | TooltipIndex`, where `TooltipIndex` is `string | null`, so the
 * index arrives as a string on some charts and has to be coerced either way.
 */
interface ChartMouseState {
  isTooltipActive?: boolean;
  activeTooltipIndex?: number | string | null;
}

/**
 * Shared hover state for a chart's active index.
 *
 * Recharts reports the active index on the chart element rather than per mark,
 * so every card wires the same two handlers; this keeps that in one place.
 */
export function useChartHover() {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  const handlers = useMemo(
    () => ({
      onMouseMove: (state: ChartMouseState) => {
        const index = Number(state?.activeTooltipIndex);
        if (state?.isTooltipActive === true && Number.isFinite(index))
          setActiveIndex(index);
      },
      onMouseLeave: () => setActiveIndex(null),
    }),
    [],
  );

  const clear = useCallback(() => setActiveIndex(null), []);

  return { activeIndex, setActiveIndex, handlers, clear };
}

export interface ChartLegendEntry {
  label: string;
  value?: ReactNode;
  /** Any CSS colour — usually `var(--color-chart-N-active)`. */
  color: string;
  /** Draw a dashed rule instead of a dot, for comparison series. */
  dashed?: boolean;
}

export function ChartLegend({
  entries,
  variant = "compact",
}: {
  entries: ChartLegendEntry[];
  variant?: "compact" | "summary";
}) {
  return (
    <dl
      className={cx(
        "flex shrink-0 flex-wrap items-center gap-4 text-text-secondary",
        variant === "summary"
          ? "justify-center text-body-regular"
          : "text-body-2-medium",
      )}
    >
      {entries.map((entry) => (
        <div key={entry.label} className="flex items-center gap-1.5">
          {entry.dashed === true ? (
            <span
              className="h-0 w-3.5 border-t-2 border-dashed"
              style={{ borderColor: entry.color }}
              aria-hidden
            />
          ) : (
            <span
              className="size-2 rounded-full"
              style={{ backgroundColor: entry.color }}
              aria-hidden
            />
          )}
          <dt>{entry.label}</dt>
          {entry.value !== undefined && (
            <dd className="text-text-primary">{entry.value}</dd>
          )}
        </div>
      ))}
    </dl>
  );
}

export interface ChartCardProps {
  /** Sits above the headline; swap it for the hovered point's own label. */
  label: string;
  /** The headline number. Counts up when it changes. */
  value: number;
  /** Rendered around the counted value, e.g. `$` or `%`. */
  prefix?: string;
  suffix?: string;
  /** Delta chip beside the headline. Omit for no chip. */
  delta?: ChartDelta;
  /** The quiet line under the headline — usually what it is being compared to. */
  caption?: string;
  legend?: ChartLegendEntry[];
  /** Anything to the right of the header instead of a legend — a control, say. */
  action?: ReactNode;
  /** Re-triggers the headline's fade when it changes; pass the active index. */
  pulseKey?: string | number | null;
  /** Format the counted headline value. */
  formatValue?: (value: number) => string;
  /** Keep fractional values for a custom formatter. Defaults to integer counting. */
  roundValue?: boolean;
  children: ReactNode;
  className?: string;
  /** Default 344px, matching the dashboard row. */
  height?: number | "auto";
}

export function ChartCard({
  label,
  value,
  prefix = "",
  suffix = "",
  delta,
  caption,
  legend,
  action,
  pulseKey = null,
  formatValue = formatNumber,
  roundValue = true,
  children,
  className,
  height = 344,
}: ChartCardProps) {
  const display = useCountUp(
    roundValue ? Math.round(value) : value,
    320,
    roundValue,
  );

  return (
    <CardSurface
      className={cx("flex min-w-0 flex-1 flex-col gap-6", className)}
      style={height === "auto" ? undefined : { height }}
    >
      <div className="flex w-full flex-col gap-3 sm:flex-row sm:items-start sm:gap-0.5">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <p className="w-full text-body-medium text-text-secondary">{label}</p>
          <div className="flex w-full items-center gap-2">
            <p
              key={pulseKey ?? "total"}
              className="animate-number-fade text-title-1-medium whitespace-nowrap text-text-primary tabular-nums"
            >
              {prefix}
              {formatValue(display)}
              {suffix}
            </p>
            {delta !== undefined && (
              <Chip variant="bold" color={delta.color}>
                {delta.label}
              </Chip>
            )}
          </div>
          {caption !== undefined && (
            <p className="text-body-2-medium text-text-tertiary tabular-nums">
              {caption}
            </p>
          )}
        </div>

        {action ?? (legend !== undefined && <ChartLegend entries={legend} />)}
      </div>

      <div className="min-h-0 w-full flex-1">{children}</div>
    </CardSurface>
  );
}

/** Axis styling every Recharts card shares. */
export const AXIS = {
  tickLine: false,
  axisLine: false,
  tick: { fontSize: 12, fill: "var(--color-text-tertiary)" },
} as const;

/** The dashed vertical rule Recharts draws under the pointer. */
export const CURSOR = {
  stroke: "var(--color-chart-cursor)",
  strokeWidth: 1,
  strokeDasharray: "4 4",
} as const;
