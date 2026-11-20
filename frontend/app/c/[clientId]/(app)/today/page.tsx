"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { BriefList } from "@/components/brief/brief-list";
import { ChangeTile } from "@/components/brief/what-changed";
import { CoachMarks, type CoachStep } from "@/components/shell/coach-marks";
import { NoticeList } from "@/components/states/notice-list";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/states/states";
import { ThinDataPrompt } from "@/components/states/thin-data-prompt";
import { CalcNote } from "@/components/ui/calc-mark";
import { Eyebrow } from "@/components/ui/surface";
import { clientQuery, newsQuery, outliersQuery, ownPerformanceQuery, recommendationsQuery, refreshQuery } from "@/lib/api/queries";
import { BRIEF_CONFIG, composeBrief } from "@/lib/brief";
import { formatRatio } from "@/lib/format";
import { CALC_MARK } from "@/lib/labels";
import { useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";

const COACH_STEPS: CoachStep[] = [
  { id: "today-window", target: '[data-coach="window"]', text: "Change how far back we look. Wider windows find more evidence in quiet niches." },
  { id: "today-confidence", target: '[data-coach="confidence"]', text: "How sure we are, and how many outlier videos and channels back it." },
  { id: "today-evidence", target: '[data-coach="brief-row"]', text: "Open any action to see the videos behind every number." },
];

function briefEyebrow(now: number): string {
  const time = new Date(now).toLocaleString("en-GB", { weekday: "long", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
  return `Your brief · ${time}`;
}

export default function TodayPage() {
  const clientId = useClientId();
  const firstBrief = useSearchParams().get("first") === "1";
  const { windowDays, lens } = useViewParams();
  const [now] = useState(() => Date.now());
  const client = useQuery(clientQuery(clientId));
  const recommendations = useQuery(recommendationsQuery(clientId, { window_days: windowDays, lens: lens === "both" ? undefined : lens }));
  const news = useQuery(newsQuery(clientId));
  const refresh = useQuery(refreshQuery(clientId));
  const own = useQuery(ownPerformanceQuery(clientId, windowDays));
  const outliers = useQuery(outliersQuery(clientId, windowDays));

  const envelope = recommendations.data?.state === "ready" ? recommendations.data.envelope : undefined;
  const thinNotice = envelope?.notices.find((notice) => notice.code === "thin_data") ?? null;
  const items = composeBrief({
    clientId,
    recommendations: envelope?.data.items ?? [],
    news: news.data?.data.items ?? [],
    refresh: refresh.data?.data.items ?? [],
    setupGap: null,
    thinNotice,
    now,
    limit: BRIEF_CONFIG.maxItems + BRIEF_CONFIG.alsoWorthALook,
  });
  const brief = items.slice(0, BRIEF_CONFIG.maxItems);
  const more = items.slice(BRIEF_CONFIG.maxItems);
  const channelsSeen = new Set(envelope?.data.items.flatMap((rec) => rec.reference_videos.map((video) => video.channel_id))).size;
  const openNews = news.data?.data.items.filter((item) => item.urgency !== "evergreen_window" && Date.parse(item.expires_at) > now).length;
  const ownReport = own.data?.data.youtube_public;
  const title = firstBrief ? "Your first brief is ready." : `Today for ${client.data?.name ?? "your business"}.`;

  return (
    <>
      <PageHeader
        eyebrow={<Eyebrow className="mb-2">{briefEyebrow(now)}</Eyebrow>}
        title={<span className="text-greeting">{title}</span>}
        subtitle={`${brief.length} ${brief.length === 1 ? "action" : "actions"}. Evidence from ${channelsSeen}${CALC_MARK} channels in the last ${windowDays} days.`}
      />
      <FreshnessLine envelope={envelope} />
      {envelope ? <NoticeList notices={envelope.notices} className="mb-5" /> : null}

      <section aria-labelledby="what-changed" className="mb-8">
        <h2 id="what-changed" className="mb-3 text-eyebrow text-muted-foreground">
          What changed
        </h2>
        <div className="grid gap-3 sm:grid-cols-3">
          {client.data?.own_channel ? (
            <ChangeTile
              origin="YouTube public"
              label="Your videos vs your normal"
              value={formatRatio(ownReport?.median_ratio_window)}
              comparison={`was ${formatRatio(ownReport?.median_ratio_prev_window)} the window before · at day ${ownReport?.comparison_age_days ?? 7}`}
            />
          ) : (
            <ChangeTile origin="YouTube public" label="Your channel" value="–" comparison="Connect your channel to compare against your own normal." />
          )}
          <ChangeTile origin="YouTube public" label="Competitor outliers" value={outliers.data?.data.total ?? "–"} comparison={`in the last ${windowDays} days`} />
          <ChangeTile origin="News" label="Open opportunities" value={openNews ?? "–"} comparison="closing within the week" />
        </div>
        <CalcNote className="mt-2" />
      </section>

      <section aria-labelledby="next-steps">
        <h2 id="next-steps" className="mb-3 text-eyebrow text-muted-foreground">
          What to do next
        </h2>
        {recommendations.isPending ? <LoadingBlock /> : null}
        {recommendations.error ? <ErrorState error={recommendations.error} onRetry={() => recommendations.refetch()} /> : null}
        {brief.length ? <BriefList items={brief} now={now} /> : null}
        {brief.length ? <CalcNote className="mt-2" /> : null}
        {envelope && !brief.length && !thinNotice ? (
          <EmptyState
            title="Nothing met our evidence bar"
            body={`No idea in the last ${windowDays} days had 3 or more recent reference videos. Adding 2–3 more channels to watch gives us more to learn from.`}
          />
        ) : null}
        {thinNotice && !brief.some((item) => item.id === "thin") ? (
          <div className="mt-4">
            <ThinDataPrompt notice={thinNotice} />
          </div>
        ) : null}
        {more.length ? (
          <details className="group mt-6">
            <summary className="flex w-fit cursor-pointer list-none items-center gap-2 rounded-control text-body-medium text-muted-foreground hover:text-foreground">
              <span aria-hidden className="transition-transform group-open:rotate-90">›</span>
              Also worth a look · {more.length}
            </summary>
            <div className="mt-3">
              <BriefList items={more} now={now} startAt={BRIEF_CONFIG.maxItems + 1} />
            </div>
          </details>
        ) : null}
      </section>
      <CoachMarks steps={COACH_STEPS} active={firstBrief && brief.length > 0} />
    </>
  );
}
