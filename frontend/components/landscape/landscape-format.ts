import type { ChannelOverview, GrowthFigure } from "@/lib/api/types";
import { formatCompact } from "@/lib/format";

export function formatPct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "–" : `${(value * 100).toFixed(digits)}%`;
}

export function formatSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(0)}%`;
}

export function formatSigned(value: number): string {
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${formatCompact(Math.abs(value))}`;
}

export function formatDays(days: number | null | undefined): string {
  if (days === null || days === undefined) return "–";
  if (days < 1) return "today";
  return `${Math.round(days)} d ago`;
}

export function formatMix(overview: Pick<ChannelOverview, "format_mix">): string {
  const shorts = overview.format_mix.short_form ?? 0;
  const long = overview.format_mix.long_form ?? 0;
  const live = overview.format_mix.live ?? 0;
  return [`${shorts} Shorts`, `${long} long`, live ? `${live} live` : null].filter(Boolean).join(" · ");
}

// growth only spans the days we actually hold, so the period is always spelled out
export function growthLabel(growth: GrowthFigure | null | undefined): { value: string; detail: string } {
  if (!growth) return { value: "–", detail: "building history: needs two days of snapshots" };
  const full = growth.days_covered >= growth.requested_days - 7;
  const period = full ? "last 12 months" : `last ${growth.days_covered} days`;
  const source = growth.source === "youtube_analytics" ? "YouTube Analytics" : "daily public snapshots";
  return { value: `${formatSigned(growth.change)} (${formatSignedPct(growth.change_pct)})`, detail: `${period} · ${source}` };
}
