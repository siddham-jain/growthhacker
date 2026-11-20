"use client";

import { useState } from "react";
import { ChartCard, ChartPlot } from "@/components/ui/primitives";
import type { CadenceWeek, ChannelScorecard } from "@/lib/api/types";
import { formatDate } from "@/lib/format";
import { ChartTableToggle, DataTable } from "./chart-table-toggle";
import { linear, PLOT_PADDING } from "./scale";

function Plot({ weeks, width, height, active, onActive }: { weeks: CadenceWeek[]; width: number; height: number; active: number | null; onActive: (index: number | null) => void }) {
  const max = Math.max(4, ...weeks.map((week) => week.uploads));
  const y = linear([0, max], [height - PLOT_PADDING.bottom, PLOT_PADDING.top]);
  const slot = (width - PLOT_PADDING.left - PLOT_PADDING.right) / weeks.length;
  const bar = Math.max(4, Math.min(28, slot * 0.55));
  return (
    <svg width={width} height={height} role="img" aria-label={`Uploads per week for the last ${weeks.length} weeks`}>
      {[0, Math.round(max / 2), max].map((tick) => (
        <g key={tick}>
          <line x1={PLOT_PADDING.left} x2={width - PLOT_PADDING.right} y1={y(tick)} y2={y(tick)} stroke="var(--border)" />
          <text x={PLOT_PADDING.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--subtle-foreground)">
            {tick}
          </text>
        </g>
      ))}
      {weeks.map((week, index) => {
        const left = PLOT_PADDING.left + index * slot + (slot - bar) / 2;
        const top = y(week.uploads);
        return (
          <g key={week.week_start} onMouseEnter={() => onActive(index)} onMouseLeave={() => onActive(null)}>
            <rect x={left} y={PLOT_PADDING.top} width={bar} height={height - PLOT_PADDING.bottom - PLOT_PADDING.top} fill="transparent" />
            <rect x={left} y={top} width={bar} height={Math.max(0, height - PLOT_PADDING.bottom - top)} rx={4} fill={active === index ? "var(--chart-1)" : "color-mix(in oklab, var(--chart-1) 55%, var(--card))"}>
              <title>{`Week of ${formatDate(week.week_start)}: ${week.uploads} uploads`}</title>
            </rect>
          </g>
        );
      })}
      {[0, weeks.length - 1].map((index) => (
        <text key={index} x={PLOT_PADDING.left + index * slot + slot / 2} y={height - 6} textAnchor={index ? "end" : "start"} fontSize={11} fill="var(--subtle-foreground)">
          {formatDate(weeks[index].week_start)}
        </text>
      ))}
    </svg>
  );
}

export function CadenceChart({ weeks, scorecard }: { weeks: CadenceWeek[]; scorecard: ChannelScorecard }) {
  const [active, setActive] = useState<number | null>(null);
  const hovered = active === null ? null : weeks[active];
  if (!weeks.length) return null;
  return (
    <ChartCard
      className="bg-card shadow-card"
      height="auto"
      label={hovered ? `Week of ${formatDate(hovered.week_start)}` : "Uploads per week"}
      value={hovered ? hovered.uploads : scorecard.uploads_per_week}
      roundValue={Boolean(hovered)}
      formatValue={(value) => (hovered ? String(value) : value.toFixed(1))}
      pulseKey={active}
      caption={
        hovered
          ? "uploads that week"
          : `average over ${weeks.length} weeks · longest gap ${scorecard.longest_gap_days ?? "–"} days · last upload ${scorecard.days_since_last_upload} days ago`
      }
    >
      <ChartTableToggle
        label="weekly uploads"
        chart={
          <div className="flex h-44 flex-col">
            <ChartPlot>{({ width, height }) => <Plot weeks={weeks} width={width} height={height} active={active} onActive={setActive} />}</ChartPlot>
          </div>
        }
        table={<DataTable columns={["Week of", "Uploads"]} rows={weeks.map((week) => [formatDate(week.week_start), week.uploads])} />}
      />
    </ChartCard>
  );
}
