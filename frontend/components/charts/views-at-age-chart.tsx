"use client";

import { useState } from "react";
import { CalcNote } from "@/components/ui/calc-mark";
import { ChartCard, ChartPlot } from "@/components/ui/primitives";
import { MethodBadge } from "@/components/ui/chips";
import type { ViewsAtAgeCurve } from "@/lib/api/types";
import { formatCompact, formatExact, formatRatio } from "@/lib/format";
import { CALC_MARK, METHOD_LABELS } from "@/lib/labels";
import { ChartTableToggle, DataTable } from "./chart-table-toggle";
import { linear, niceCeiling, PLOT_PADDING, ticks } from "./scale";

const SERIES = ["var(--chart-1)", "var(--chart-3)", "var(--chart-6)"];

interface ViewsAtAgeChartProps {
  curves: ViewsAtAgeCurve[];
  title: string;
  baseline: { age_days: number; views: number | null } | null;
  baselineLabel: string;
}

function Plot({ curves, baseline, width, height, active, onActive }: { curves: ViewsAtAgeCurve[]; baseline: ViewsAtAgeChartProps["baseline"]; width: number; height: number; active: number | null; onActive: (index: number | null) => void }) {
  const maxAge = Math.max(7, ...curves.flatMap((curve) => curve.points.map((point) => point.age_days)));
  const maxViews = niceCeiling(Math.max(baseline?.views ?? 0, ...curves.flatMap((curve) => curve.points.map((point) => point.views))));
  const x = linear([0, maxAge], [PLOT_PADDING.left, width - PLOT_PADDING.right]);
  const y = linear([0, maxViews], [height - PLOT_PADDING.bottom, PLOT_PADDING.top]);
  return (
    <svg width={width} height={height} role="img" aria-label={`Views by age in days for ${curves.length} videos. Dashed segments and hollow points are proxies.`}>
      {ticks(maxViews).map((tick) => (
        <g key={tick}>
          <line x1={PLOT_PADDING.left} x2={width - PLOT_PADDING.right} y1={y(tick)} y2={y(tick)} stroke="var(--border)" />
          <text x={PLOT_PADDING.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--subtle-foreground)">
            {formatCompact(tick)}
          </text>
        </g>
      ))}
      {[0, 7, 14, 21, 28].filter((age) => age <= maxAge).map((age) => (
        <text key={age} x={x(age)} y={height - 6} textAnchor={age === 0 ? "start" : x(age) > width - 40 ? "end" : "middle"} fontSize={11} fill="var(--subtle-foreground)">
          day {age}
        </text>
      ))}
      {baseline?.views ? (
        <g>
          <line x1={x(baseline.age_days)} x2={x(baseline.age_days)} y1={PLOT_PADDING.top} y2={height - PLOT_PADDING.bottom} stroke="var(--border-strong)" strokeDasharray="3 4" />
          <circle cx={x(baseline.age_days)} cy={y(baseline.views)} r={4} fill="var(--foreground)" />
        </g>
      ) : null}
      {curves.map((curve, index) => {
        const color = SERIES[index % SERIES.length];
        const dim = active !== null && active !== index;
        return (
          <g key={curve.video_id} opacity={dim ? 0.25 : 1} onMouseEnter={() => onActive(index)} onMouseLeave={() => onActive(null)}>
            {curve.points.slice(1).map((point, pointIndex) => {
              const previous = curve.points[pointIndex];
              const proxy = METHOD_LABELS[point.method].proxy || METHOD_LABELS[previous.method].proxy;
              return <line key={point.age_days} x1={x(previous.age_days)} y1={y(previous.views)} x2={x(point.age_days)} y2={y(point.views)} stroke={color} strokeWidth={2} strokeDasharray={proxy ? "4 4" : undefined} />;
            })}
            {curve.points.map((point) => {
              const proxy = METHOD_LABELS[point.method].proxy;
              return (
                <circle key={point.age_days} cx={x(point.age_days)} cy={y(point.views)} r={3.5} fill={proxy ? "var(--card)" : color} stroke={color} strokeWidth={1.5}>
                  <title>{`${curve.title}, day ${point.age_days}: ${formatExact(point.views)} views (${METHOD_LABELS[point.method].label})`}</title>
                </circle>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

// how views accumulate by age; proxies are drawn dashed and hollow so an estimate never reads as a measurement
export function ViewsAtAgeChart({ curves, title, baseline, baselineLabel }: ViewsAtAgeChartProps) {
  const [active, setActive] = useState<number | null>(null);
  const hovered = active === null ? null : curves[active];
  const dayValue = (curve: ViewsAtAgeCurve, age: number) => curve.points.find((point) => point.age_days === age);
  if (!curves.length) return null;
  return (
    <ChartCard
      className="bg-card shadow-card"
      height="auto"
      label={hovered ? hovered.title : title}
      value={hovered ? (dayValue(hovered, 7)?.views ?? hovered.points.at(-1)?.views ?? 0) : (baseline?.views ?? 0)}
      formatValue={(value) => formatCompact(value)}
      pulseKey={active}
      caption={`${hovered ? `views at day 7 · ${formatRatio(hovered.outlier_ratio)} its channel's normal` : baselineLabel} ${CALC_MARK}`}
      legend={[
        { label: "Measured", color: "var(--chart-1)" },
        { label: "Proxy", color: "var(--subtle-foreground)", dashed: true },
        ...(baseline?.views ? [{ label: `Normal at day ${baseline.age_days}`, color: "var(--foreground)" }] : []),
      ]}
    >
      <ChartTableToggle
        label="views by age"
        chart={
          <div className="flex h-56 flex-col">
            <ChartPlot>{({ width, height }) => <Plot curves={curves} baseline={baseline} width={width} height={height} active={active} onActive={setActive} />}</ChartPlot>
          </div>
        }
        table={
          <DataTable
            columns={["Video", "Day", "Views", "Method"]}
            rows={curves.flatMap((curve) =>
              curve.points.map((point) => [<span key="t" dir="auto">{curve.title}</span>, point.age_days, formatExact(point.views), <MethodBadge key="m" method={point.method} />]),
            )}
          />
        }
      />
      <ol className="mt-2 flex flex-col gap-1 text-caption-1-regular text-muted-foreground">
        {curves.map((curve, index) => (
          <li key={curve.video_id} className="flex gap-2">
            <span aria-hidden className="mt-1.5 size-2 shrink-0 rounded-full" style={{ background: SERIES[index % SERIES.length] }} />
            <span className="line-clamp-1" dir="auto">
              {curve.title}
            </span>
          </li>
        ))}
      </ol>
      <CalcNote className="mt-2" />
    </ChartCard>
  );
}
