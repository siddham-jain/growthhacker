"use client";

import type { ConfidenceLabel, RecType } from "@/lib/api/types";

import { ConfidenceMeter } from "@/components/ui/confidence-meter";
import { TypeChip } from "@/components/ui/chips";
import { EntityChip, ValuePill } from "@/components/ui/primitives";
import { formatCompact, formatRatio } from "@/lib/format";
import type { InsightView, LiveRunState } from "@/lib/runs/run-reducer";

function InsightRow({ insight }: { insight: InsightView }) {
  const { payload } = insight;
  switch (payload.kind) {
    case "videos_fetched":
      return (
        <p>
          <EntityChip name={payload.channel_title} /> {payload.count} videos in {payload.window_days} days
        </p>
      );
    case "outlier_found":
      return (
        <p>
          <EntityChip name={payload.channel_title} /> “{payload.title}” ran <ValuePill tone="green">{formatRatio(payload.outlier_ratio)}</ValuePill> their normal ·{" "}
          {formatCompact(payload.views_at_age.views)} {payload.views_at_age.label}
        </p>
      );
    case "trait_lift":
      return (
        <p>
          {payload.trait}: <ValuePill tone="accent">{formatRatio(payload.lift)}</ValuePill> more common among outliers ({payload.outliers_with_trait} outliers, {payload.sample} videos)
        </p>
      );
    case "news_found":
      return (
        <p>
          <ValuePill tone="orange">News</ValuePill> {payload.headline} <span className="text-subtle-foreground">· {payload.source_domain}</span>
        </p>
      );
    case "refresh_suggested":
      return (
        <p>
          <ValuePill>Refresh</ValuePill> “{payload.title}”: {payload.insertions} line to add
        </p>
      );
    case "card_written":
      return (
        <div className="flex flex-col gap-1.5 rounded-control bg-card p-3 shadow-card">
          <div className="flex flex-wrap items-center gap-2">
            <TypeChip type={payload.type as RecType} />
            <ConfidenceMeter confidence={{ label: payload.confidence_label as ConfidenceLabel, outliers: 0, channels: 0 }} compact />
          </div>
          <p className="text-body-medium text-foreground">{payload.summary}</p>
        </div>
      );
  }
}

export function InsightStream({ state }: { state: LiveRunState }) {
  if (!state.insights.length) return null;
  const cards = state.insights.filter((item) => item.payload.kind === "card_written");
  const evidence = state.insights.filter((item) => item.payload.kind !== "card_written").slice(-8);
  return (
    <>
      <section aria-label="What we found" className="flex flex-col gap-2">
        <h3 className="text-eyebrow text-muted-foreground">Proof of work</h3>
        <ul className="flex flex-col gap-2 text-body-regular text-foreground">
          {evidence.map((insight) => (
            <li key={insight.seq} className="animate-reveal">
              <InsightRow insight={insight} />
            </li>
          ))}
        </ul>
      </section>
      {cards.length ? (
        <section aria-label="Recommendations written" className="flex flex-col gap-2">
          <h3 className="text-eyebrow text-muted-foreground">Recommendations · {cards.length}</h3>
          <ul className="flex flex-col gap-2">
            {cards.map((insight) => (
              <li key={insight.seq} className="animate-reveal">
                <InsightRow insight={insight} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
