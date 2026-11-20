"use client";

import Link from "next/link";
import { Fragment, useState } from "react";
import { CardSurface, ChartAxisLabel } from "@/components/ui/primitives";
import { OriginChip } from "@/components/ui/chips";
import type { GapMatrixView } from "@/lib/api/types";
import { formatRatio, plural } from "@/lib/format";
import { cx } from "@/lib/cx";

function tint(videos: number, max: number) {
  const share = max ? Math.min(1, videos / max) : 0;
  return videos ? `color-mix(in oklab, var(--chart-1) ${Math.round(10 + share * 40)}%, var(--card))` : "var(--muted)";
}

// fork of zeus heatmap-chart-card: buyer questions × competitors, cell = their videos on it; counts only
export function GapMatrix({ matrix, clientId }: { matrix: GapMatrixView; clientId: string }) {
  const [active, setActive] = useState<{ topic: string; channel: string } | null>(null);
  const max = Math.max(1, ...Object.values(matrix.cells).flatMap((row) => Object.values(row).map((cell) => cell.n_videos)));
  const activeCell = active ? matrix.cells[active.topic]?.[active.channel] : undefined;
  const activeTopic = matrix.topics.find((topic) => topic.id === active?.topic);
  const activeChannel = matrix.channels.find((channel) => channel.channel_id === active?.channel);

  return (
    <CardSurface tone="primary" padding="uniform" className="flex flex-col gap-4 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-section text-foreground">Gap matrix</h2>
          <p className="text-caption-1-regular text-muted-foreground" aria-live="polite">
            {activeCell && activeTopic && activeChannel
              ? `${activeChannel.title} on “${activeTopic.label}”: ${plural(activeCell.n_videos, "video")}, ${plural(activeCell.n_outliers, "outlier")}${activeCell.best_ratio ? `, best ${formatRatio(activeCell.best_ratio)}` : ""}`
              : "Videos each competitor posted on your buyer questions. A ● marks outliers. Benchmarks are left out."}
          </p>
        </div>
        <OriginChip origin="YouTube public" />
      </div>
      <div className="overflow-x-auto">
        <div
          role="table"
          aria-label="Videos per buyer question and competitor"
          className="grid min-w-[720px] gap-1"
          style={{ gridTemplateColumns: `minmax(220px, 1.6fr) repeat(${matrix.channels.length}, minmax(64px, 1fr)) minmax(64px, 1fr)` }}
        >
          <div role="row" className="contents">
            <span role="columnheader" className="sticky left-0 bg-card" />
            {matrix.channels.map((channel) => (
              <ChartAxisLabel key={channel.channel_id} role="columnheader" active={active?.channel === channel.channel_id} className="truncate px-1 pb-1 text-center" title={channel.title}>
                {channel.title.split(" ")[0]}
              </ChartAxisLabel>
            ))}
            <ChartAxisLabel role="columnheader" className="px-1 pb-1 text-center text-foreground">
              You
            </ChartAxisLabel>
          </div>
          {matrix.topics.map((topic) => {
            const yours = matrix.own[topic.id] ?? 0;
            const theyCover = matrix.channels.some((channel) => (matrix.cells[topic.id]?.[channel.channel_id]?.n_videos ?? 0) > 0);
            return (
              <Fragment key={topic.id}>
                <div role="row" className="contents">
                  <ChartAxisLabel role="rowheader" active={active?.topic === topic.id} className="sticky left-0 z-10 flex items-center gap-2 bg-card py-1 pe-2 text-body-regular" title={topic.label}>
                    <span className="line-clamp-2">{topic.label}</span>
                  </ChartAxisLabel>
                  {matrix.channels.map((channel) => {
                    const cell = matrix.cells[topic.id]?.[channel.channel_id] ?? { n_videos: 0, n_outliers: 0, best_ratio: null };
                    return (
                      <span key={channel.channel_id} role="cell" className="flex">
                        <Link
                          href={`/c/${clientId}/competitors/${channel.channel_id}?tab=gap`}
                          onMouseEnter={() => setActive({ topic: topic.id, channel: channel.channel_id })}
                          onMouseLeave={() => setActive(null)}
                          onFocus={() => setActive({ topic: topic.id, channel: channel.channel_id })}
                          onBlur={() => setActive(null)}
                          className={cx(
                            "grid h-11 w-full place-items-center rounded-[6px] text-caption-1-medium tabular-nums transition-shadow",
                            "text-foreground",
                            active?.topic === topic.id && active.channel === channel.channel_id && "ring-2 ring-ring",
                          )}
                          style={{ backgroundColor: tint(cell.n_videos, max) }}
                        >
                          <span className="sr-only">{`${channel.title}, ${topic.label}: ${plural(cell.n_videos, "video")}, ${plural(cell.n_outliers, "outlier")}`}</span>
                          <span aria-hidden>
                            {cell.n_videos}
                            {cell.n_outliers ? <sup className="ml-0.5">●{cell.n_outliers}</sup> : null}
                          </span>
                        </Link>
                      </span>
                    );
                  })}
                  <span role="cell" className={cx("grid h-11 place-items-center rounded-[6px] border text-caption-1-medium tabular-nums", yours ? "border-border text-foreground" : "border-dashed border-border-strong text-muted-foreground")}>
                    {yours || (theyCover ? <span className="text-caption-2-medium text-warning">gap</span> : "0")}
                  </span>
                </div>
              </Fragment>
            );
          })}
        </div>
      </div>
      <p className="text-caption-1-regular text-muted-foreground">
        A “gap” is a question your competitors cover and you don&apos;t yet. Open a competitor to see their videos on it, or see{" "}
        <Link href={`/c/${clientId}/ideas`} className="text-info underline underline-offset-2">
          ideas that answer it
        </Link>
        .
      </p>
    </CardSurface>
  );
}
