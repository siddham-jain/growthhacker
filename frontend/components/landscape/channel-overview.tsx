import type { ReactNode } from "react";
import { CalcNote } from "@/components/ui/calc-mark";
import { OriginChip } from "@/components/ui/chips";
import type { ChannelOverview } from "@/lib/api/types";
import { formatCompact } from "@/lib/format";
import { formatDays, formatMix, formatPct, formatSignedPct, growthLabel } from "./landscape-format";

const FORMAT_LABELS: Record<string, string> = { short_form: "Shorts", long_form: "Long-form", live: "Live replays" };

function Tile({ label, value, detail }: { label: string; value: ReactNode; detail?: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-card bg-card p-4 shadow-card">
      <span className="text-caption-1-medium text-muted-foreground">{label}</span>
      <span className="text-title-3-medium tabular-nums text-foreground">
        {value}
      </span>
      {detail ? <span className="text-caption-1-regular text-muted-foreground">{detail}</span> : null}
    </div>
  );
}

function versus(value: number | null | undefined, median: number | null | undefined, format: (value: number) => string): string | undefined {
  return median === null || median === undefined || value === null || value === undefined ? undefined : `competitor median ${format(median)}`;
}

export function ChannelOverviewPanel({ overview, median }: { overview: ChannelOverview; median: Record<string, number | null> }) {
  const subs = growthLabel(overview.subscriber_growth);
  const views = growthLabel(overview.view_growth);
  const formats = Object.entries(overview.by_format);
  return (
    <section aria-labelledby="overview-heading" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="overview-heading" className="text-eyebrow text-muted-foreground">
          Channel at a glance · last {overview.window_days} days
        </h2>
        <OriginChip origin="YouTube public" />
        {overview.subscriber_growth?.source === "youtube_analytics" ? <OriginChip origin="YouTube Analytics (owner)" /> : null}
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Subscribers" value={formatCompact(overview.subscribers)} detail={overview.subscribers === null ? "hidden by the channel" : `${formatCompact(overview.total_views)} lifetime views`} />
        <Tile label="Subscriber growth" value={subs.value} detail={subs.detail} />
        <Tile label="View growth" value={views.value} detail={views.detail} />
        <Tile label="Format mix" value={formatMix(overview)} detail={`${overview.overall.uploads} uploads`} />
        <Tile
          label="Avg views per upload"
          value={formatCompact(overview.overall.mean_views)}
          detail={versus(overview.overall.mean_views, median.mean_views, formatCompact) ?? `median ${formatCompact(overview.overall.median_views)}`}
        />
        <Tile label="Avg engagement" value={formatPct(overview.overall.mean_engagement_rate)} detail={versus(overview.overall.mean_engagement_rate, median.mean_engagement_rate, (v) => formatPct(v)) ?? "likes + comments per view"} />
        <Tile
          label="Upload pace"
          value={`${overview.overall.uploads_per_week.toFixed(1)}/week`}
          detail={overview.pace_change_pct === null ? `last upload ${formatDays(overview.days_since_last_upload)}` : `${formatSignedPct(overview.pace_change_pct)} vs previous ${overview.window_days} days`}
        />
        <Tile label="Views per subscriber" value={formatPct(overview.views_per_subscriber)} detail="avg views per upload ÷ subscribers" />
      </div>
      {formats.length ? (
        <div className="overflow-x-auto rounded-card bg-card p-4 shadow-card">
          <table className="w-full text-left text-caption-1-regular tabular-nums">
            <thead>
              <tr>
                {["Format", "Uploads", "Per week", "Avg views", "Median views", "Avg engagement", "Share of views"].map((column) => (
                  <th key={column} scope="col" className="border-b border-border px-2 py-1.5 font-medium text-muted-foreground">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {formats.map(([format, stats]) => (
                <tr key={format} className="border-b border-border last:border-0">
                  <th scope="row" className="px-2 py-1.5 font-medium text-foreground">
                    {FORMAT_LABELS[format] ?? format}
                  </th>
                  <td className="px-2 py-1.5">{stats.uploads}</td>
                  <td className="px-2 py-1.5">{stats.uploads_per_week.toFixed(1)}</td>
                  <td className="px-2 py-1.5">{formatCompact(stats.mean_views)}</td>
                  <td className="px-2 py-1.5">{formatCompact(stats.median_views)}</td>
                  <td className="px-2 py-1.5">{formatPct(stats.mean_engagement_rate)}</td>
                  <td className="px-2 py-1.5">{formatPct(overview.views_share[format], 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <CalcNote />
      <p className="text-caption-1-regular text-subtle-foreground">
        Views are current lifetime counts, so older uploads have had longer to collect them. YouTube rounds public subscriber counts.
        {overview.scraped_uploads ? ` ${overview.scraped_uploads} uploads were read from youtube.com because the YouTube API was unavailable.` : ""}
      </p>
    </section>
  );
}
