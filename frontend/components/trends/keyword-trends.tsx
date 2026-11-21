"use client";

import { RiArrowDownSLine } from "@remixicon/react";
import { useState } from "react";
import type { KeywordTrend } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { formatCompact, plural } from "@/lib/format";
import { HeatChip, SourceChips, TrendVideoGrid } from "./trend-parts";

function KeywordRow({ trend }: { trend: KeywordTrend }) {
  const [open, setOpen] = useState(false);
  const empty = !trend.videos.length;
  return (
    <li className="flex flex-col gap-2 py-3">
      <button type="button" aria-expanded={open} disabled={empty} onClick={() => setOpen(!open)} className="flex w-full flex-wrap items-center gap-2 text-left disabled:cursor-default">
        <RiArrowDownSLine aria-hidden className={cx("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180", empty && "invisible")} />
        <span className="text-body-medium text-foreground">{trend.candidate.keyword}</span>
        <HeatChip heat={trend.heat} />
        <SourceChips sources={trend.candidate.sources} />
        <span className="ml-auto text-caption-1-regular text-muted-foreground tabular-nums">
          {empty
            ? `No new videos in ${trend.window_days} days`
            : `${plural(trend.recent_videos, "new video")} · fastest ${formatCompact(Math.round(trend.leaders_views_per_day ?? 0))}/day${trend.competitor_videos ? ` · ${trend.competitor_videos} from competitors` : ""}`}
        </span>
      </button>
      {open ? (
        <div className="animate-reveal pl-6">
          <TrendVideoGrid videos={trend.videos} />
        </div>
      ) : null}
    </li>
  );
}

const COLLAPSED = 6;

export function KeywordTrends({ trends }: { trends: KeywordTrend[] }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? trends : trends.slice(0, COLLAPSED);
  return (
    <section aria-labelledby="trending-keywords" className="flex flex-col rounded-card bg-card p-4 shadow-card sm:p-5">
      <h2 id="trending-keywords" className="text-eyebrow text-muted-foreground">
        Keywords we checked on YouTube
      </h2>
      <ul className="divide-y divide-border">
        {shown.map((trend) => (
          <KeywordRow key={trend.candidate.keyword} trend={trend} />
        ))}
      </ul>
      {trends.length > COLLAPSED ? (
        <button type="button" className="w-fit pt-2 text-body-medium text-info" onClick={() => setShowAll(!showAll)}>
          {showAll ? "Show fewer" : `Show all ${trends.length} keywords`}
        </button>
      ) : null}
    </section>
  );
}
