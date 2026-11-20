"use client";

import { useQuery } from "@tanstack/react-query";
import { ReferenceStrip } from "@/components/cards/reference-strip";
import { RefreshCard } from "@/components/cards/refresh-card";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { OriginChip } from "@/components/ui/chips";
import { Tab, TabList, TabPanel, Tabs } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { clientQuery, ownPerformanceQuery, refreshQuery } from "@/lib/api/queries";
import type { ReferenceVideo, VideoMetricsView } from "@/lib/api/types";
import { formatRatio } from "@/lib/format";
import { useTabParam, useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";

const TABS = ["performance", "refresh", "conversion"] as const;

function asReference(video: VideoMetricsView): ReferenceVideo {
  return {
    video_id: video.video_id,
    url: `https://www.youtube.com/watch?v=${video.video_id}`,
    title: video.title,
    channel: video.channel_title,
    channel_id: video.channel_id,
    channel_role: "own",
    published_at: video.published_at,
    views_day7: video.views_at_age.views,
    views_at_age: video.views_at_age,
    outlier_ratio: video.outlier_ratio,
  };
}

export default function ChannelPage() {
  const clientId = useClientId();
  const { windowDays, lens } = useViewParams();
  const [tab, setTab] = useTabParam(TABS, lens === "conversion" ? "conversion" : "performance");
  const client = useQuery(clientQuery(clientId));
  const own = useQuery(ownPerformanceQuery(clientId, windowDays));
  const refresh = useQuery(refreshQuery(clientId));
  const report = own.data?.data.youtube_public;
  const suggestions = (refresh.data?.data.items ?? []).filter((item) => item.basis !== "rule_based");

  return (
    <>
      <PageHeader title="Your channel" subtitle="How are your videos doing against your own normal, and which old videos deserve a small update?" />
      <FreshnessLine envelope={own.data} />
      <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(key as (typeof TABS)[number])}>
        <TabList aria-label="Your channel sections">
          <Tab id="performance">Performance</Tab>
          <Tab id="refresh" count={suggestions.length}>Refresh suggestions</Tab>
          <Tab id="conversion">Conversion</Tab>
        </TabList>
        <TabPanel id="performance">
          {own.isPending ? <LoadingBlock /> : null}
          {own.error ? <ErrorState error={own.error} onRetry={() => own.refetch()} /> : null}
          {report ? (
            <div className="flex flex-col gap-6">
              <div className="flex flex-wrap items-end gap-6 rounded-card bg-card p-5 shadow-card">
                <div>
                  <OriginChip origin="YouTube public" />
                  <p className="mt-2 text-metric text-foreground">{formatRatio(report.median_ratio_window)}</p>
                  <p className="text-caption-1-regular text-muted-foreground">
                    median of your last {report.videos_in_window} videos vs your normal, views at day {report.comparison_age_days}
                  </p>
                </div>
                <p className="text-body-regular text-muted-foreground">Window before: {formatRatio(report.median_ratio_prev_window)}</p>
              </div>
              <section>
                <h2 className="mb-3 text-eyebrow text-muted-foreground">Best against your normal</h2>
                <ReferenceStrip videos={report.best.map(asReference)} />
              </section>
              <details className="rounded-card bg-card p-4 shadow-card">
                <summary className="cursor-pointer text-body-medium">Below your normal · {report.worst.length}</summary>
                <div className="mt-3">
                  <ReferenceStrip videos={report.worst.map(asReference)} />
                </div>
              </details>
            </div>
          ) : null}
        </TabPanel>
        <TabPanel id="refresh">
          <div className="flex flex-col gap-4">
            {refresh.isPending ? <LoadingBlock /> : null}
            {suggestions.map((suggestion) => (
              <RefreshCard key={suggestion.id} suggestion={suggestion} />
            ))}
            {refresh.data && !suggestions.length ? (
              <EmptyState title="No updates needed" body="Your descriptions don't need updates right now. Businesses like yours rarely need them; we check monthly." />
            ) : null}
          </div>
        </TabPanel>
        <TabPanel id="conversion">
          {client.data?.oauth_connected ? null : (
            <EmptyState
              title="Connect your channel"
              body="Connect your channel to see subscribers gained, traffic sources and search terms. Only your workspace sees them, and they stay separate from public YouTube numbers."
              action={
                <Button variant="primary" size="small" onClick={() => api.integrations.youtubeConnect(clientId)}>
                  Connect with YouTube
                </Button>
              }
            />
          )}
        </TabPanel>
      </Tabs>
    </>
  );
}
