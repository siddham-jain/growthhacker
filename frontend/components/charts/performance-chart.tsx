"use client";

import { useState } from "react";
import { ChartCard, ChartPlot } from "@/components/ui/primitives";
import { MethodBadge } from "@/components/ui/chips";
import type { OwnPerformanceReport } from "@/lib/api/types";
import { formatDate, formatRatio } from "@/lib/format";
import { METHOD_LABELS } from "@/lib/labels";
import { ChartTableToggle, DataTable } from "./chart-table-toggle";
import { linear, PLOT_PADDING } from "./scale";

const BASELINE = 1;

function Plot({ report, width, height, active, onActive }: { report: OwnPerformanceReport; width: number; height: number; active: number | null; onActive: (index: number | null) => void }) {
  const points = report.videos.filter((video) => video.ratio !== null);
  const times = points.map((video) => Date.parse(video.published_at));
  const maxRatio = Math.max(2, ...points.map((video) => video.ratio ?? 0)) * 1.1;
  const x = linear([Math.min(...times), Math.max(...times)], [PLOT_PADDING.left, width - PLOT_PADDING.right]);
  const y = linear([0, maxRatio], [height - PLOT_PADDING.bottom, PLOT_PADDING.top]);
  const yTicks = [0, 0.5, 1, 1.5, 2, 3, 4, 5].filter((tick) => tick <= maxRatio && (maxRatio < 3 || Number.isInteger(tick)));
  const path = points.map((video, index) => `${index ? "L" : "M"}${x(times[index])},${y(video.ratio ?? 0)}`).join(" ");
  return (
    <svg width={width} height={height} role="img" aria-label={`Each of your ${points.length} videos against your normal at day ${report.comparison_age_days}; the dashed line is your normal (1.0×).`}>
      {yTicks.map((tick) => (
        <g key={tick}>
          <line x1={PLOT_PADDING.left} x2={width - PLOT_PADDING.right} y1={y(tick)} y2={y(tick)} stroke="var(--border)" strokeWidth={1} />
          <text x={PLOT_PADDING.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--subtle-foreground)">
            {tick}×
          </text>
        </g>
      ))}
      <line x1={PLOT_PADDING.left} x2={width - PLOT_PADDING.right} y1={y(BASELINE)} y2={y(BASELINE)} stroke="var(--foreground)" strokeWidth={1.5} strokeDasharray="5 5" />
      <text x={PLOT_PADDING.left + 4} y={y(BASELINE) + 14} textAnchor="start" fontSize={11} fill="var(--muted-foreground)">
        your normal
      </text>
      <path d={path} fill="none" stroke="var(--chart-1)" strokeOpacity={0.35} strokeWidth={1.5} />
      {points.map((video, index) => {
        const proxy = METHOD_LABELS[video.method].proxy;
        const isActive = active === index;
        return (
          <circle
            key={video.video_id}
            cx={x(times[index])}
            cy={y(video.ratio ?? 0)}
            r={isActive ? 6.5 : 5}
            fill={proxy ? "var(--card)" : "var(--chart-1)"}
            stroke="var(--chart-1)"
            strokeWidth={2}
            strokeDasharray={proxy ? "2 2" : undefined}
            onMouseEnter={() => onActive(index)}
            onMouseLeave={() => onActive(null)}
          >
            <title>{`${video.title}: ${formatRatio(video.ratio)} (${METHOD_LABELS[video.method].label})`}</title>
          </circle>
        );
      })}
      {[0, points.length - 1].map((index) =>
        points[index] ? (
          <text key={index} x={x(times[index])} y={height - 6} textAnchor={index ? "end" : "start"} fontSize={11} fill="var(--subtle-foreground)">
            {formatDate(points[index].published_at)}
          </text>
        ) : null,
      )}
    </svg>
  );
}

// each video's views at the comparison age divided by the channel's own median at that age, as reported by the server
export function PerformanceChart({ report }: { report: OwnPerformanceReport }) {
  const [active, setActive] = useState<number | null>(null);
  const points = report.videos.filter((video) => video.ratio !== null);
  const hovered = active === null ? null : points[active];
  const proxies = report.method_mix.age_band_proxy ?? 0;
  return (
    <ChartCard
      className="bg-card shadow-card"
      height="auto"
      label={hovered ? hovered.title : "Your videos vs your normal"}
      value={hovered?.ratio ?? report.median_ratio_window ?? 0}
      roundValue={false}
      formatValue={(value) => `${value.toFixed(1)}×`}
      pulseKey={active}
      caption={
        hovered
          ? `${formatDate(hovered.published_at)} · ${METHOD_LABELS[hovered.method].label.toLowerCase()}`
          : `median of ${report.videos_in_window} videos at day ${report.comparison_age_days} · was ${formatRatio(report.median_ratio_prev_window)} the window before`
      }
      legend={[
        { label: "Measured", color: "var(--chart-1)" },
        { label: "Proxy (estimate)", color: "var(--subtle-foreground)" },
        { label: "Your normal", color: "var(--foreground)", dashed: true },
      ]}
    >
      <ChartTableToggle
        label="your videos"
        chart={
          <div className="flex h-56 flex-col">
            <ChartPlot>{({ width, height }) => <Plot report={report} width={width} height={height} active={active} onActive={setActive} />}</ChartPlot>
          </div>
        }
        table={
          <DataTable
            columns={["Video", "Published", `vs normal at day ${report.comparison_age_days}`, "Method"]}
            rows={report.videos.map((video) => [
              <span key="title" dir="auto">{video.title}</span>,
              formatDate(video.published_at),
              formatRatio(video.ratio),
              <MethodBadge key="method" method={video.method} />,
            ])}
          />
        }
      />
      {proxies ? (
        <p className="mt-2 text-caption-1-regular text-muted-foreground">
          {proxies} of {report.videos_in_window} values are proxies: we started tracking those videos after day {report.comparison_age_days}, so their day-{report.comparison_age_days} views are estimated from their age band. Hollow dots mark them.
        </p>
      ) : null}
    </ChartCard>
  );
}
