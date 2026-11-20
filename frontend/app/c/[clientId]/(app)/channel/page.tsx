"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { RefreshList } from "@/components/cards/refresh-list";
import { VideoList } from "@/components/cards/video-list";
import { YourAnswerCitations } from "@/components/citations/answer-citations";
import { PerformanceChart, ViewsAtAgeChart } from "@/components/charts/lazy";
import { VideoEvidenceDrawer } from "@/components/evidence/lazy-drawers";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { OriginChip } from "@/components/ui/chips";
import { Tab, TabList, TabPanel, Tabs } from "@/components/ui/primitives";
import { clientQuery, ownPerformanceQuery, refreshQuery } from "@/lib/api/queries";
import type { VideoMetricsView } from "@/lib/api/types";
import { useTabParam, useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";
import { startYouTubeConnect } from "@/lib/youtube-connect";

const TABS = ["performance", "refresh", "conversion"] as const;

function ConnectPanel({ clientId }: { clientId: string }) {
  return (
    <section className="flex flex-col gap-3 rounded-card bg-card p-5 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-body-medium text-foreground">Subscribers gained, traffic sources, search terms</h3>
        <OriginChip origin="YouTube Analytics (owner)" />
      </div>
      <p className="text-body-regular text-muted-foreground">
        Connect your channel to see what brings viewers and which searches find you. Only your workspace sees these, and they stay separate from public YouTube numbers.
      </p>
      <Button variant="primary" size="small" className="w-fit" onClick={() => startYouTubeConnect(clientId)}>
        Connect with YouTube
      </Button>
    </section>
  );
}

export default function ChannelPage() {
  const clientId = useClientId();
  const { windowDays, lens } = useViewParams();
  const [tab, setTab] = useTabParam(TABS, lens === "conversion" ? "conversion" : "performance");
  const [inspected, setInspected] = useState<VideoMetricsView | null>(null);
  const client = useQuery(clientQuery(clientId));
  const own = useQuery(ownPerformanceQuery(clientId, windowDays));
  const refresh = useQuery(refreshQuery(clientId));
  const report = own.data?.data.youtube_public;
  const suggestions = (refresh.data?.data.items ?? []).filter((item) => item.basis !== "rule_based");
  const openCount = suggestions.filter((item) => item.status === "open").length;

  if (client.data && !client.data.own_channel) {
    return (
      <>
        <PageHeader title="Your channel" subtitle="How are your videos doing against your own normal?" />
        <EmptyState
          title="No YouTube channel yet"
          body="That's fine. We compare against similar channels until you have your own baseline. Add your channel in Settings once it's live."
        />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Your channel" subtitle="How are your videos doing against your own normal, and which old videos deserve a small update?" />
      <FreshnessLine envelope={own.data} />
      <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(key as (typeof TABS)[number])}>
        <TabList aria-label="Your channel sections">
          <Tab id="performance">Performance</Tab>
          <Tab id="refresh" count={openCount}>
            Refresh suggestions
          </Tab>
          <Tab id="conversion">Conversion</Tab>
        </TabList>
        <TabPanel id="performance">
          {own.isPending ? <LoadingBlock /> : null}
          {own.error ? <ErrorState error={own.error} onRetry={() => own.refetch()} /> : null}
          {report ? (
            <div className="flex flex-col gap-6">
              <div className="grid items-start gap-4 xl:grid-cols-2">
                <PerformanceChart report={report} />
                <ViewsAtAgeChart
                  curves={report.curves}
                  title="How your recent videos picked up views"
                  baseline={report.baseline}
                  baselineLabel={report.baseline ? `your normal at day ${report.baseline.age_days}, the median of ${report.baseline.n} videos` : "no baseline yet"}
                />
              </div>
              <section aria-labelledby="best-heading">
                <h2 id="best-heading" className="mb-3 text-eyebrow text-muted-foreground">
                  Best against your normal
                </h2>
                <VideoList videos={report.best} onInspect={setInspected} />
              </section>
              <details className="rounded-card bg-card p-4 shadow-card">
                <summary className="cursor-pointer text-body-medium">Below your normal · {report.worst.length}</summary>
                <div className="mt-3">
                  <VideoList videos={report.worst} onInspect={setInspected} />
                </div>
              </details>
            </div>
          ) : null}
        </TabPanel>
        <TabPanel id="refresh">
          <div className="flex flex-col gap-4">
            {refresh.isPending ? <LoadingBlock /> : null}
            {suggestions.length ? (
              <p className="text-body-regular text-muted-foreground">Your niche moves fast enough that a line or two can help old videos show up for new searches. We only ever add text; nothing you wrote is removed.</p>
            ) : null}
            {suggestions.length ? <RefreshList clientId={clientId} suggestions={suggestions} /> : null}
            {refresh.data && !suggestions.length ? (
              <EmptyState title="No updates needed" body="Your descriptions don't need updates right now. Businesses like yours rarely need them; we check monthly." />
            ) : null}
          </div>
        </TabPanel>
        <TabPanel id="conversion">
          <div className="grid gap-4 lg:grid-cols-3">
            {client.data?.oauth_connected && own.data?.data.youtube_analytics ? (
              <section className="flex flex-col gap-2 rounded-card bg-card p-5 shadow-card">
                <OriginChip origin="YouTube Analytics (owner)" />
                <h3 className="text-body-medium text-foreground">Searches that found you</h3>
                <p className="text-body-regular text-muted-foreground">{own.data.data.youtube_analytics.top_search_terms.slice(0, 5).join(" · ") || "No search terms yet"}</p>
                <p className="text-caption-1-regular text-muted-foreground">last {windowDays} days · only your workspace sees this</p>
              </section>
            ) : (
              <ConnectPanel clientId={clientId} />
            )}
            <section className="flex flex-col gap-2 rounded-card bg-card p-5 shadow-card">
              <OriginChip origin="YouTube public" />
              <h3 className="text-body-medium text-foreground">Calls to action in your descriptions</h3>
              <p className="text-body-regular text-muted-foreground">Booking links, helplines and chapters, checked on every recent video. Fixes live in Channel hygiene.</p>
            </section>
            <section className="flex flex-col gap-2 rounded-card bg-card p-5 shadow-card">
              <OriginChip origin="Website" />
              <h3 className="text-body-medium text-foreground">Website actions</h3>
              <p className="text-body-regular text-muted-foreground">Bookings and calls happen on your site, not on YouTube. We list them in your profile but never mix them into video numbers.</p>
            </section>
            <YourAnswerCitations clientId={clientId} />
          </div>
        </TabPanel>
      </Tabs>
      <VideoEvidenceDrawer video={inspected} onClose={() => setInspected(null)} />
    </>
  );
}
