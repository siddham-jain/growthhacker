"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { OrbState } from "thinking-orbs";
import { InfoNote } from "@/components/states/states";
import { LinkButton } from "@/components/ui/button";
import { ActiveBeam, AnalysisOrb } from "@/components/ui/effects";
import { LoadingState, useCountUp } from "@/components/ui/primitives";
import { Eyebrow } from "@/components/ui/surface";
import type { ChannelVerifiedBy, RunStatus } from "@/lib/api/types";
import { formatCompact } from "@/lib/format";
import type { LiveRunState } from "@/lib/runs/run-reducer";
import { useRunStream } from "@/lib/runs/use-run-stream";
import { CandidateStrip } from "./candidate-strip";
import { FactStream } from "./fact-stream";
import { InsightStream } from "./insight-stream";
import { SourceCascade } from "./source-cascade";
import { StageRail } from "./stage-rail";

const ORB_BY_STAGE: Record<string, OrbState> = {
  crawl: "searching",
  profile: "weaving",
  resolve: "connecting",
  discover: "searching",
  ingest: "working",
  metrics: "solving",
  patterns: "shaping",
  news: "searching",
  write: "composing",
  refresh: "working",
};

const VERIFIED_BY_TEXT: Record<ChannelVerifiedBy, string> = {
  website_link: "confirmed by a link on your website",
  channel_link: "the channel links back to your website",
  oauth: "confirmed through YouTube sign-in",
  manual: "added by you",
};

const SUCCESS: RunStatus[] = ["succeeded", "partial", "awaiting_confirmation"];
const ROUTE_DELAY_MS = 1_200;
const ANNOUNCE_EVERY_MS = 5_000;

function activeStage(state: LiveRunState): string | undefined {
  return state.stageOrder.find((id) => state.stages[id]?.status === "running");
}

function etaLabel(state: LiveRunState): string | null {
  if (!state.startedAt || state.percent < 20 || state.percent >= 100) return null;
  const elapsed = Date.now() - Date.parse(state.startedAt);
  const remainingSeconds = Math.round(((elapsed / state.percent) * (100 - state.percent)) / 30_000) * 30;
  if (remainingSeconds < 10) return null;
  return remainingSeconds < 90 ? "About a minute left" : `About ${Math.round(remainingSeconds / 60)} minutes left`;
}

function summary(state: LiveRunState): string {
  const pages = state.sources.reduce((sum, step) => sum + step.pages.length, 0);
  const parts = [state.latestMessage ?? "Starting"];
  if (pages) parts.push(`read ${pages} pages`);
  if (state.facts.length) parts.push(`learned ${state.facts.length} kinds of facts`);
  if (state.candidates.length) parts.push(`found ${state.candidates.length} competitors`);
  return parts.join("; ");
}

function useThrottledAnnouncement(state: LiveRunState): string {
  const latest = useRef(state);
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    latest.current = state;
  }, [state]);
  useEffect(() => {
    const timer = setInterval(() => setAnnouncement(summary(latest.current)), ANNOUNCE_EVERY_MS);
    return () => clearInterval(timer);
  }, []);
  return announcement;
}

function ProgressBar({ percent }: { percent: number }) {
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-secondary" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)} aria-label="Analysis progress">
      <div className="h-full rounded-full bg-info transition-[width] duration-500 ease-out" style={{ width: `${percent}%` }} />
    </div>
  );
}

interface LiveAnalysisProps {
  runId: string;
  step: string;
  headline: ReactNode;
  subtitle: string;
  doneHref: string;
  doneLabel: string;
}

export function LiveAnalysis({ runId, step, headline, subtitle, doneHref, doneLabel }: LiveAnalysisProps) {
  const router = useRouter();
  const { state, error } = useRunStream(runId);
  const announcement = useThrottledAnnouncement(state);
  const finished = SUCCESS.includes(state.status);
  const failed = state.status === "failed" || state.status === "cancelled";
  const stage = activeStage(state);
  const eta = etaLabel(state);
  const shownPercent = useCountUp(Math.round(state.percent), 600);

  useEffect(() => {
    if (!finished) return;
    const timer = setTimeout(() => router.push(doneHref), ROUTE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [finished, doneHref, router]);

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(320px,440px)_1fr] lg:gap-12">
      <div className="order-2 flex flex-col gap-6 lg:order-1">
        <div className="flex flex-col gap-3">
          <Eyebrow dot>{step}</Eyebrow>
          <h1 className="text-display text-foreground">{headline}</h1>
          <p className="text-body-regular text-muted-foreground">{subtitle}</p>
        </div>
        <StageRail state={state} />
        {state.connection === "reconnecting" || state.connection === "polling" ? (
          <p className="text-caption-1-regular text-subtle-foreground">Reconnecting… we&apos;ll pick up where we left off.</p>
        ) : null}
        {error ? <InfoNote tone="warning">We couldn&apos;t load this run. It may have finished or been removed.</InfoNote> : null}
      </div>

      <div className="order-1 rounded-hero p-3 shadow-hero sm:p-5 lg:order-2" style={{ background: "var(--hero-surface)" }}>
        <ActiveBeam active={!finished && !failed}>
          <div className="flex flex-col gap-6 rounded-panel bg-card p-5 sm:p-6">
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <AnalysisOrb state={finished ? "breathing" : (ORB_BY_STAGE[stage ?? ""] ?? "working")} label="Analysis in progress" />
                <div className="min-w-0 flex-1">
                  {finished ? (
                    <p className="text-body-medium text-foreground">{state.latestMessage}</p>
                  ) : (
                    <LoadingState label={state.latestMessage ?? "Starting"} startedAt={state.startedAt ? Date.parse(state.startedAt) : undefined} />
                  )}
                </div>
                <span className="text-caption-1-medium text-muted-foreground tabular-nums">{shownPercent}%</span>
              </div>
              <ProgressBar percent={state.percent} />
              {eta ? <p className="text-caption-1-regular text-subtle-foreground">{eta}</p> : null}
            </div>

            <SourceCascade state={state} />
            <FactStream state={state} />
            {state.channel ? (
              <p className="animate-reveal text-body-regular text-foreground">
                Your channel: <strong className="font-medium">{state.channel.title}</strong> ({state.channel.handle}
                {state.channel.subscriber_count ? `, ${formatCompact(state.channel.subscriber_count)} subscribers` : ""}), {VERIFIED_BY_TEXT[state.channel.verified_by]}.
              </p>
            ) : null}
            <CandidateStrip state={state} />
            <InsightStream state={state} />
            {state.notices.map((notice) => (
              <InfoNote key={notice.seq} tone={notice.level === "info" ? "info" : "warning"}>
                {notice.message}
              </InfoNote>
            ))}

            {finished ? (
              <LinkButton href={doneHref} variant="primary" size="medium" className="w-fit">
                {doneLabel}
              </LinkButton>
            ) : null}
            {failed ? <InfoNote tone="warning">Something went wrong while {state.latestMessage?.toLowerCase() ?? "working"}. What we found so far is saved.</InfoNote> : null}

            <p className="border-t border-border pt-4 text-caption-1-regular text-subtle-foreground">
              Live: read from your website and YouTube&apos;s public data. You can leave; progress is saved.
            </p>
          </div>
        </ActiveBeam>
      </div>
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>
    </div>
  );
}
