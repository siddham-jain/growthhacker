"use client";

import { useQuery } from "@tanstack/react-query";
import { RiGridLine } from "@remixicon/react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { CompetitorFolder } from "@/components/competitors/competitor-folder";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/states/states";
import { Button, LinkButton } from "@/components/ui/button";
import { Chip, SegmentedControl, SegmentedControlItem } from "@/components/ui/primitives";
import { gapsQuery, scorecardsQuery } from "@/lib/api/queries";
import type { ChannelScorecard } from "@/lib/api/types";
import { formatRatio } from "@/lib/format";
import { RELATION_LABELS } from "@/lib/labels";
import { useTabParam, useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";

const GapMatrix = dynamic(() => import("@/components/charts/gap-matrix").then((module) => module.GapMatrix), { loading: () => <LoadingBlock rows={1} /> });

const SORTS = ["outliers", "cadence", "median", "name"] as const;
const VIEWS = ["folders", "gaps"] as const;
type Sort = (typeof SORTS)[number];

const SORTERS: Record<Sort, (a: ChannelScorecard, b: ChannelScorecard) => number> = {
  outliers: (a, b) => b.outliers_in_window - a.outliers_in_window,
  cadence: (a, b) => b.uploads_per_week - a.uploads_per_week,
  median: (a, b) => (b.median_ratio ?? 0) - (a.median_ratio ?? 0),
  name: (a, b) => a.channel.title.localeCompare(b.channel.title),
};

export default function CompetitorsPage() {
  const clientId = useClientId();
  const { windowDays } = useViewParams();
  const [sort, setSort] = useTabParam(SORTS, "outliers", "sort");
  const [view, setView] = useTabParam(VIEWS, "folders", "view");
  const scorecards = useQuery(scorecardsQuery(clientId, windowDays));
  const gaps = useQuery({ ...gapsQuery(clientId, windowDays), enabled: view === "gaps" });
  const all = scorecards.data?.data ?? [];
  const competitors = all.filter((card) => card.role === "competitor_set").sort(SORTERS[sort]);
  const watching = all.filter((card) => card.role !== "competitor_set");

  return (
    <>
      <PageHeader
        title="Competitors"
        subtitle="How are your competitors doing? Cadence, performance, what's working. Open a folder for the full evidence."
        controls="window"
        actions={
          <Button variant="secondary" size="small" leadingIcon={RiGridLine} aria-pressed={view === "gaps"} onClick={() => setView(view === "gaps" ? "folders" : "gaps")}>
            {view === "gaps" ? "Hide gap matrix" : "Gap matrix"}
          </Button>
        }
      />
      <FreshnessLine envelope={scorecards.data} />
      {view === "gaps" ? (
        <div className="mb-8">
          {gaps.isPending ? <LoadingBlock rows={1} /> : null}
          {gaps.error ? <ErrorState error={gaps.error} onRetry={() => gaps.refetch()} /> : null}
          {gaps.data ? <GapMatrix matrix={gaps.data.data} clientId={clientId} /> : null}
        </div>
      ) : null}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-caption-1-medium text-muted-foreground">Sort by</span>
        <SegmentedControl aria-label="Sort competitors" selectedKeys={[sort]} onSelectionChange={(keys) => setSort([...keys][0] as Sort)}>
          <SegmentedControlItem id="outliers">Outliers</SegmentedControlItem>
          <SegmentedControlItem id="cadence">Cadence</SegmentedControlItem>
          <SegmentedControlItem id="median">Median ratio</SegmentedControlItem>
          <SegmentedControlItem id="name">Name</SegmentedControlItem>
        </SegmentedControl>
      </div>
      {scorecards.isPending ? <LoadingBlock /> : null}
      {scorecards.error ? <ErrorState error={scorecards.error} onRetry={() => scorecards.refetch()} /> : null}
      {scorecards.data && !competitors.length ? (
        <EmptyState
          title="No competitors tracked yet"
          body="Add 3 you know, or pick from channels that answer your buyer questions."
          action={<LinkButton href={`/c/${clientId}/settings?tab=competitors`}>Add competitors</LinkButton>}
        />
      ) : null}
      <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
        {competitors.map((card) => (
          <li key={card.channel.channel_id} className="flex flex-col gap-2">
            <CompetitorFolder
              name={card.channel.title}
              href={`/c/${clientId}/competitors/${card.channel.channel_id}?w=${windowDays}`}
              badge={card.relation ? RELATION_LABELS[card.relation] : undefined}
              subtitle={card.channel.handle}
              previews={card.top_outliers.map((video) => ({ videoId: video.video_id, title: video.title, badge: formatRatio(video.outlier_ratio) }))}
              count={card.outliers_in_window}
              countLabel={`outliers of ${card.videos_in_window} videos`}
              meta={`${card.uploads_per_week}/week`}
            />
            <p className="px-1 text-caption-1-regular text-muted-foreground">
              median {formatRatio(card.median_ratio)} their normal
              {card.days_since_last_upload > 45 ? ` · no uploads in ${card.days_since_last_upload} days` : ""}
              {card.subscriber_hidden ? " · subscribers hidden" : ""}
            </p>
          </li>
        ))}
      </ul>
      {watching.length ? (
        <details className="mt-8 rounded-card bg-card p-4 shadow-card">
          <summary className="cursor-pointer text-body-medium text-foreground">Also watching · {watching.length}</summary>
          <ul className="mt-3 flex flex-col divide-y divide-border">
            {watching.map((card) => (
              <li key={card.channel.channel_id} className="flex flex-wrap items-center gap-2 py-2.5">
                <Link href={`/c/${clientId}/competitors/${card.channel.channel_id}`} className="text-body-medium text-foreground hover:underline">
                  {card.channel.title}
                </Link>
                <Chip variant="caption" color="soft">
                  {card.role === "benchmark_set" ? "Benchmark: used for format and title ideas only" : "Niche"}
                </Chip>
                <span className="ml-auto text-caption-1-regular text-muted-foreground">
                  {card.outliers_in_window} outliers · {card.uploads_per_week}/week
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </>
  );
}
