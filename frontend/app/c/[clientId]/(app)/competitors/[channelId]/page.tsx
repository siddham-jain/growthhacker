"use client";

import { useQuery } from "@tanstack/react-query";
import { RiArrowLeftLine, RiExternalLinkLine } from "@remixicon/react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ReferenceStrip } from "@/components/cards/reference-strip";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { ErrorState, LoadingBlock } from "@/components/states/states";
import { Chip, Tab, TabList, TabPanel, Tabs } from "@/components/ui/primitives";
import { competitorDetailQuery } from "@/lib/api/queries";
import type { ReferenceVideo, VideoMetricsView } from "@/lib/api/types";
import { formatCompact, formatRatio } from "@/lib/format";
import { RELATION_LABELS } from "@/lib/labels";
import { useTabParam, useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";

const TABS = ["overview", "working", "outliers", "gap"] as const;

function toReference(video: VideoMetricsView): ReferenceVideo {
  return {
    video_id: video.video_id,
    url: `https://www.youtube.com/watch?v=${video.video_id}`,
    title: video.title,
    channel: video.channel_title,
    channel_id: video.channel_id,
    published_at: video.published_at,
    views_day7: video.views_at_age.views,
    views_at_age: video.views_at_age,
    outlier_ratio: video.outlier_ratio,
  };
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-card bg-card px-4 py-3 shadow-card">
      <p className="text-caption-1-regular text-muted-foreground">{label}</p>
      <p className="mt-1 text-section text-foreground">{value}</p>
    </div>
  );
}

export default function CompetitorDetailPage() {
  const clientId = useClientId();
  const { channelId } = useParams<{ channelId: string }>();
  const { windowDays } = useViewParams();
  const [tab, setTab] = useTabParam(TABS, "overview");
  const detail = useQuery(competitorDetailQuery(clientId, channelId, windowDays));
  const data = detail.data?.data;
  const card = data?.scorecard;

  return (
    <>
      <Link href={`/c/${clientId}/competitors`} className="mb-3 inline-flex items-center gap-1 text-body-medium text-muted-foreground hover:text-foreground">
        <RiArrowLeftLine aria-hidden className="size-4" /> Competitors
      </Link>
      <PageHeader
        title={card?.channel.title ?? "Competitor"}
        subtitle={card ? `${card.channel.handle} · ${RELATION_LABELS[card.relation]} competitor` : "Loading…"}
        actions={
          card ? (
            <a href={`https://www.youtube.com/${card.channel.handle}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-body-medium text-info">
              Open on YouTube <RiExternalLinkLine aria-hidden className="size-4" />
            </a>
          ) : null
        }
      />
      <FreshnessLine envelope={detail.data} />
      {detail.isPending ? <LoadingBlock /> : null}
      {detail.error ? <ErrorState error={detail.error} onRetry={() => detail.refetch()} /> : null}
      {data && card ? (
        <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(key as (typeof TABS)[number])}>
          <TabList aria-label="Competitor sections">
            <Tab id="overview">Overview</Tab>
            <Tab id="working" count={data.trait_lift.length}>What&apos;s working</Tab>
            <Tab id="outliers" count={data.top_outliers.length}>Their outliers</Tab>
            <Tab id="gap">Gap vs your questions</Tab>
          </TabList>
          <TabPanel id="overview">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Fact label="Uploads per week" value={String(card.uploads_per_week)} />
              <Fact label="Median vs their own normal" value={formatRatio(card.median_ratio)} />
              <Fact label={`Outliers in ${windowDays} days`} value={`${card.outliers_in_window} of ${card.videos_in_window}`} />
              <Fact label="Last upload" value={`${card.days_since_last_upload} days ago`} />
            </div>
            <p className="mt-3 text-caption-1-regular text-muted-foreground">
              {card.subscriber_hidden ? "Subscribers hidden" : `${formatCompact(card.channel.subscriber_count)} subscribers`} · YouTube public data
            </p>
          </TabPanel>
          <TabPanel id="working">
            <ul className="flex flex-col gap-2">
              {data.trait_lift.map((trait) => (
                <li key={trait.trait_key} className="flex flex-wrap items-center gap-2 rounded-card bg-card px-4 py-3 shadow-card">
                  <span className="text-body-medium text-foreground">{trait.label}</span>
                  <span className="text-body-regular text-muted-foreground">
                    {trait.outliers_with} of {trait.outliers_total} outliers vs {trait.n_with} of {trait.n_total} videos ({formatRatio(trait.lift)} lift)
                  </span>
                  <Chip variant="caption" color={trait.status === "supported" ? "lime" : "soft"} className="ml-auto">
                    {trait.status === "supported" ? "Supported" : "Exploratory"}
                  </Chip>
                </li>
              ))}
            </ul>
          </TabPanel>
          <TabPanel id="outliers">
            <p className="mb-3 text-caption-1-regular text-muted-foreground">Titles are shown as evidence only; we never suggest copying them.</p>
            <ReferenceStrip videos={data.top_outliers.map(toReference)} />
          </TabPanel>
          <TabPanel id="gap">
            <ul className="flex flex-col divide-y divide-border rounded-card bg-card shadow-card">
              {data.topic_coverage.map((row) => (
                <li key={row.topic} className="flex flex-wrap items-center gap-2 px-4 py-3">
                  <span className="min-w-0 flex-1 text-body-regular text-foreground">{row.topic}</span>
                  <span className="text-caption-1-regular text-muted-foreground">
                    they: {row.videos} videos, {row.outliers} outliers · you: {row.client_videos}
                  </span>
                  {row.client_videos === 0 && row.videos > 0 ? (
                    <Chip variant="caption" color="orange">
                      You haven&apos;t covered this
                    </Chip>
                  ) : null}
                </li>
              ))}
            </ul>
          </TabPanel>
        </Tabs>
      ) : null}
    </>
  );
}
