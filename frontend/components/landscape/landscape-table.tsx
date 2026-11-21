import Link from "next/link";
import type { ReactNode } from "react";
import { CalcNote } from "@/components/ui/calc-mark";
import type { ChannelLandscape, ChannelOverview } from "@/lib/api/types";
import { formatCompact } from "@/lib/format";
import { cx } from "@/lib/cx";
import { formatDays, formatPct, formatSignedPct, growthLabel } from "./landscape-format";

const COLUMNS = ["Channel", "Subscribers", "Shorts", "Long-form", "Uploads/week", "Pace vs prior 90 d", "Avg views", "Median views", "Avg engagement", "Views/sub", "Sub growth", "View growth", "Last upload"];

function growthCell(overview: ChannelOverview, metric: "subscriber_growth" | "view_growth"): ReactNode {
  const growth = growthLabel(overview[metric]);
  return overview[metric] ? <span title={growth.detail}>{formatSignedPct(overview[metric]?.change_pct)}</span> : <span title={growth.detail}>–</span>;
}

function cells(overview: ChannelOverview): ReactNode[] {
  return [
    formatCompact(overview.subscribers),
    overview.format_mix.short_form ?? 0,
    overview.format_mix.long_form ?? 0,
    overview.overall.uploads_per_week.toFixed(1),
    formatSignedPct(overview.pace_change_pct),
    formatCompact(overview.overall.mean_views),
    formatCompact(overview.overall.median_views),
    formatPct(overview.overall.mean_engagement_rate),
    formatPct(overview.views_per_subscriber),
    growthCell(overview, "subscriber_growth"),
    growthCell(overview, "view_growth"),
    formatDays(overview.days_since_last_upload),
  ];
}

function medianCells(median: Record<string, number | null>): ReactNode[] {
  const value = (key: string, format: (v: number) => string) => (median[key] === null || median[key] === undefined ? "–" : format(median[key] as number));
  return [
    "–",
    value("short_form_uploads", (v) => v.toFixed(0)),
    value("long_form_uploads", (v) => v.toFixed(0)),
    value("uploads_per_week", (v) => v.toFixed(1)),
    "–",
    value("mean_views", formatCompact),
    value("median_views", formatCompact),
    value("mean_engagement_rate", (v) => formatPct(v)),
    value("views_per_subscriber", (v) => formatPct(v)),
    value("subscriber_growth_pct", formatSignedPct),
    value("view_growth_pct", formatSignedPct),
    value("days_since_last_upload", formatDays),
  ];
}

function Row({ name, values, emphasis }: { name: ReactNode; values: ReactNode[]; emphasis?: "own" | "median" }) {
  return (
    <tr className={cx("border-b border-border last:border-0", emphasis === "own" && "bg-muted/40", emphasis === "median" && "text-muted-foreground")}>
      <th scope="row" className="whitespace-nowrap px-2 py-2 text-left font-medium text-foreground">
        {name}
      </th>
      {values.map((value, index) => (
        <td key={index} className="whitespace-nowrap px-2 py-2">
          {value}
        </td>
      ))}
    </tr>
  );
}

// channels side by side in name order with the competitor median as a reference row; no rank, no score
export function LandscapeTable({ landscape, clientId }: { landscape: ChannelLandscape; clientId: string }) {
  return (
    <section aria-labelledby="landscape-heading" className="flex flex-col gap-2">
      <h2 id="landscape-heading" className="text-eyebrow text-muted-foreground">
        Landscape · last {landscape.window_days} days, growth over up to {landscape.growth_days} days
      </h2>
      <div className="overflow-x-auto rounded-card bg-card p-3 shadow-card">
        <table className="w-full min-w-[1080px] text-caption-1-regular tabular-nums">
          <thead>
            <tr>
              {COLUMNS.map((column) => (
                <th key={column} scope="col" className="whitespace-nowrap border-b border-border px-2 py-1.5 text-left font-medium text-muted-foreground">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {landscape.own ? <Row name={`You · ${landscape.own.title}`} values={cells(landscape.own)} emphasis="own" /> : null}
            {landscape.competitors.map((overview) => (
              <Row
                key={overview.channel_id}
                name={
                  <Link href={`/c/${clientId}/competitors/${overview.channel_id}`} className="hover:underline">
                    {overview.title}
                  </Link>
                }
                values={cells(overview)}
              />
            ))}
            {landscape.competitors.length > 1 ? <Row name="Competitor median" values={medianCells(landscape.competitor_median)} emphasis="median" /> : null}
          </tbody>
        </table>
      </div>
      <CalcNote />
      <p className="text-caption-1-regular text-subtle-foreground">
        Growth starts from the first day we saw each channel, so it covers fewer days until a year of history builds up; hover a growth value for its period. Your own growth comes from YouTube Analytics when the channel is connected.
      </p>
    </section>
  );
}
