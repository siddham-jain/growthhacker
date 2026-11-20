"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { RecommendationCard } from "@/components/cards/recommendation-card";
import { RecommendationEvidenceDrawer } from "@/components/evidence/evidence-drawer";
import { NoticeList } from "@/components/states/notice-list";
import { FreshnessLine } from "@/components/shell/freshness-line";
import { PageHeader } from "@/components/shell/page-header";
import { EmptyState, ErrorState, LoadingBlock } from "@/components/states/states";
import { ThinDataPrompt } from "@/components/states/thin-data-prompt";
import { Button } from "@/components/ui/button";
import { SegmentedControl, SegmentedControlItem, Tab, TabList, TabPanel, Tabs } from "@/components/ui/primitives";
import { useRecommendationFeedback } from "@/lib/api/mutations";
import { recommendationsQuery } from "@/lib/api/queries";
import type { RecType, WhiteSpaceNote } from "@/lib/api/types";
import { useEvidenceParam, useTabParam, useViewParams } from "@/lib/url-state";
import { useClientId } from "@/lib/use-client-id";

const TABS = ["all", "video_idea", "title_pattern", "thumbnail_pattern", "format"] as const;
const TAB_LABELS: Record<(typeof TABS)[number], string> = {
  all: "All",
  video_idea: "Video ideas",
  title_pattern: "Title patterns",
  thumbnail_pattern: "Thumbnail patterns",
  format: "Formats",
};
const INITIAL_VISIBLE = 3;
const STATUSES = ["open", "saved", "done"] as const;

function UnansweredPanel({ notes }: { notes: WhiteSpaceNote[] }) {
  if (!notes.length) return null;
  return (
    <aside aria-labelledby="unanswered" className="flex flex-col gap-3 self-start rounded-card bg-card p-4 shadow-card">
      <h2 id="unanswered" className="text-eyebrow text-muted-foreground">
        Unanswered on YouTube
      </h2>
      {notes.map((note) => (
        <p key={note.text} className="text-body-regular text-foreground">
          No one has answered “{note.text}” in the last {note.window_days} days{" "}
          <span className="text-muted-foreground">
            (checked {note.videos_checked} videos across {note.channels_checked} channels). No reference videos, so no confidence score.
          </span>
        </p>
      ))}
    </aside>
  );
}

export default function IdeasPage() {
  const clientId = useClientId();
  const { windowDays, lens, setLens } = useViewParams();
  const [tab, setTab] = useTabParam(TABS, "all");
  const [statusFilter, setStatusFilter] = useTabParam(STATUSES, "open", "status");
  const { openEvidence } = useEvidenceParam();
  const focusCard = useSearchParams().get("card");
  const [showAll, setShowAll] = useState(false);
  const result = useQuery(recommendationsQuery(clientId, { window_days: windowDays }));
  const feedback = useRecommendationFeedback(clientId);

  useEffect(() => {
    if (focusCard) document.getElementById(focusCard)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focusCard, result.data]);

  const envelope = result.data?.state === "ready" ? result.data.envelope : undefined;
  const all = (envelope?.data.items ?? []).filter((rec) => rec.type !== "news_opportunity" && rec.type !== "description_refresh" && rec.type !== "creator");
  const byStatus = all.filter((rec) => (statusFilter === "open" ? rec.feedback?.status !== "done" && rec.feedback?.status !== "saved" : rec.feedback?.status === statusFilter));
  const inTab = tab === "all" ? byStatus : byStatus.filter((rec) => rec.type === (tab as RecType));
  const visibleByLens = lens === "both" ? inTab : inTab.filter((rec) => rec.lens.includes(lens));
  const hiddenByLens = inTab.length - visibleByLens.length;
  const shown = showAll || focusCard ? visibleByLens : visibleByLens.slice(0, INITIAL_VISIBLE);
  const thinNotice = envelope?.notices.find((notice) => notice.code === "thin_data");

  return (
    <>
      <PageHeader title="Next videos" subtitle="What should you make next, and why will it bring buyers? Every idea links to the videos behind it." />
      <FreshnessLine envelope={envelope} />
      <div className="grid gap-6 xl:grid-cols-[1fr_300px]">
        <div className="flex min-w-0 flex-col gap-4">
          <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(key as (typeof TABS)[number])}>
            <TabList aria-label="Recommendation types">
              {TABS.map((id) => (
                <Tab key={id} id={id} count={id === "all" ? all.length : all.filter((rec) => rec.type === id).length}>
                  {TAB_LABELS[id]}
                </Tab>
              ))}
            </TabList>
            <TabPanel id={tab} className="flex flex-col gap-4 pt-4 outline-none">
          {result.isPending ? <LoadingBlock /> : null}
          {result.error ? <ErrorState error={result.error} onRetry={() => result.refetch()} /> : null}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-caption-1-medium text-muted-foreground">Show</span>
            <SegmentedControl aria-label="Status" selectedKeys={[statusFilter]} onSelectionChange={(keys) => setStatusFilter([...keys][0] as (typeof STATUSES)[number])}>
              <SegmentedControlItem id="open">To consider</SegmentedControlItem>
              <SegmentedControlItem id="saved">Saved</SegmentedControlItem>
              <SegmentedControlItem id="done">Done</SegmentedControlItem>
            </SegmentedControl>
          </div>
          {result.data?.state === "writing" ? (
            <div className="flex flex-col gap-3" role="status">
              <p className="text-body-regular text-muted-foreground">Writing cards for the last {windowDays} days… about 20 seconds.</p>
              <LoadingBlock rows={2} />
            </div>
          ) : null}
          {envelope ? <NoticeList notices={envelope.notices} /> : null}
          {thinNotice ? <ThinDataPrompt notice={thinNotice} /> : null}
          {envelope && !visibleByLens.length && !hiddenByLens && statusFilter !== "open" ? (
            <EmptyState
              title={statusFilter === "saved" ? "Nothing saved yet" : "Nothing marked done yet"}
              body={statusFilter === "saved" ? "Save an idea to keep it here while you plan." : "Mark an idea done when you publish it, and link the video so we can track it."}
            />
          ) : null}
          {envelope && !visibleByLens.length && !hiddenByLens && statusFilter === "open" ? (
            <EmptyState
              title="Nothing met our evidence bar"
              body={`No ${TAB_LABELS[tab].toLowerCase()} in the last ${windowDays} days had 3 or more recent reference videos. Widening the window or adding competitors gives us more to learn from.`}
            />
          ) : null}
          {shown.map((rec, index) => (
            <RecommendationCard
              key={rec.id}
              recommendation={rec}
              lensFilter={lens}
              defaultExpanded={rec.id === focusCard || (index === 0 && !focusCard)}
              highlighted={rec.id === focusCard}
              onFeedback={(input) => feedback.mutate({ recommendationId: rec.id, ...input })}
              onEvidence={() => openEvidence(rec.id)}
            />
          ))}
          {!showAll && !focusCard && visibleByLens.length > INITIAL_VISIBLE ? (
            <Button variant="secondary" size="small" className="w-fit" onClick={() => setShowAll(true)}>
              Show {visibleByLens.length - INITIAL_VISIBLE} more
            </Button>
          ) : null}
          {hiddenByLens ? (
            <p className="text-caption-1-regular text-muted-foreground">
              {hiddenByLens} {hiddenByLens === 1 ? "card is" : "cards are"} hidden by the {lens === "views" ? "Views" : "Conversion"} lens ·{" "}
              <button type="button" className="text-info underline underline-offset-2" onClick={() => setLens("both")}>
                Show all
              </button>
            </p>
          ) : null}
            </TabPanel>
          </Tabs>
        </div>
        <UnansweredPanel notes={envelope?.white_space ?? []} />
      </div>
      <RecommendationEvidenceDrawer clientId={clientId} />
    </>
  );
}
