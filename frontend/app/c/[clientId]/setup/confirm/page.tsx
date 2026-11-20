"use client";

import { useQuery } from "@tanstack/react-query";
import { RiArrowRightLine } from "@remixicon/react";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { CompetitorReview } from "@/components/onboarding/competitor-review";
import { OwnershipPicker } from "@/components/onboarding/ownership-picker";
import { ProfileReview } from "@/components/onboarding/profile-review";
import { TopicReview } from "@/components/onboarding/topic-review";
import { ErrorState, InfoNote, LoadingBlock } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Eyebrow } from "@/components/ui/surface";
import { api } from "@/lib/api";
import { buyerQuestionsQuery, clientQuery, clientKey, competitorsQuery, keywordsQuery, profileQuery } from "@/lib/api/queries";
import type { CompetitorView } from "@/lib/api/types";
import { RELATION_LABELS } from "@/lib/labels";
import { useClientId } from "@/lib/use-client-id";

const MIN_COMPETITORS = 3;
const MAX_COMPETITORS = 10;

function Section({ index, title, subtitle, children }: { index: number; title: string; subtitle: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`confirm-${index}`} className="flex flex-col gap-4 border-t border-border pt-8">
      <div>
        <Eyebrow>Step {index}</Eyebrow>
        <h2 id={`confirm-${index}`} className="mt-1 text-section text-foreground">
          {title}
        </h2>
        <p className="mt-1 text-body-regular text-muted-foreground">{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

function activeIds(items: { id: string; status: string }[] | undefined): Set<string> {
  return new Set((items ?? []).filter((item) => item.status !== "rejected" && item.status !== "retired").map((item) => item.id));
}

function initialDecisions(items: CompetitorView[] | undefined): Record<string, "confirmed" | "rejected"> {
  return Object.fromEntries((items ?? []).filter((item) => item.role === "competitor_set" && item.status !== "rejected").map((item) => [item.id, "confirmed" as const]));
}

function toggle(set: Set<string>, id: string): Set<string> {
  const next = new Set(set);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export default function ConfirmPage() {
  const clientId = useClientId();
  const router = useRouter();
  const client = useQuery(clientQuery(clientId));
  const profile = useQuery(profileQuery(clientId));
  const keywords = useQuery(keywordsQuery(clientId));
  const questions = useQuery(buyerQuestionsQuery(clientId));
  const competitors = useQuery(competitorsQuery(clientId));
  const needsChannel = client.data ? !client.data.own_channel?.verified : false;
  const channelRef = client.data?.website_url ?? client.data?.name ?? "";
  const resolve = useQuery({
    queryKey: [...clientKey(clientId), "channel-resolve", channelRef],
    queryFn: () => api.clients.resolveChannel(clientId, channelRef),
    enabled: needsChannel,
  });

  const [selectedKeywords, setSelectedKeywords] = useState<Set<string> | null>(null);
  const [selectedQuestions, setSelectedQuestions] = useState<Set<string> | null>(null);
  const [decisions, setDecisions] = useState<Record<string, "confirmed" | "rejected"> | null>(null);
  const [channelChoice, setChannelChoice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [idempotencyKey] = useState(() => crypto.randomUUID());

  const keywordSelection = selectedKeywords ?? activeIds(keywords.data?.items);
  const questionSelection = selectedQuestions ?? activeIds(questions.data?.items);
  const competitorDecisions = decisions ?? initialDecisions(competitors.data?.items);
  const allCompetitors = competitors.data?.items ?? [];
  const reviewable = allCompetitors.filter((item) => item.role === "competitor_set" && !(item.status === "rejected" && item.added_by === "system"));
  const alsoWatch = allCompetitors.filter((item) => item.role !== "competitor_set" && item.status !== "rejected");
  const excluded = allCompetitors.filter((item) => item.status === "rejected" && item.added_by === "system");
  const confirmedCount = Object.values(competitorDecisions).filter((decision) => decision === "confirmed").length;
  const topicCount = keywordSelection.size + questionSelection.size;

  const blocker =
    confirmedCount < MIN_COMPETITORS
      ? `Pick at least ${MIN_COMPETITORS} competitors. Channels that answer your buyer questions count too.`
      : confirmedCount > MAX_COMPETITORS
        ? `You can track up to ${MAX_COMPETITORS}. Move extras to “Also watch”.`
        : topicCount === 0
          ? "Keep at least one buyer question or keyword."
          : null;

  async function startAnalysis() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await Promise.all(
        Object.entries(competitorDecisions).map(([id, decision]) =>
          decision === "confirmed" ? api.competitors.confirm(clientId, id) : api.competitors.reject(clientId, id, { reason: "not_competitor" }),
        ),
      );
      const rejectedKeywords = (keywords.data?.items ?? []).filter((item) => !keywordSelection.has(item.id)).map((item) => item.id);
      const rejectedQuestions = (questions.data?.items ?? []).filter((item) => !questionSelection.has(item.id)).map((item) => item.id);
      await Promise.all([
        api.keywords.bulkStatus(clientId, { ids: [...keywordSelection], status: "confirmed" }),
        api.buyerQuestions.bulkStatus(clientId, { ids: [...questionSelection], status: "confirmed" }),
        rejectedKeywords.length ? api.keywords.bulkStatus(clientId, { ids: rejectedKeywords, status: "rejected" }) : null,
        rejectedQuestions.length ? api.buyerQuestions.bulkStatus(clientId, { ids: rejectedQuestions, status: "rejected" }) : null,
      ]);
      if (needsChannel && channelChoice) await api.clients.attachChannel(clientId, channelChoice);
      const { run } = await api.clients.confirmSetup(clientId, client.data?.window_days, idempotencyKey);
      router.push(`/c/${clientId}/setup/analysis?run=${run.id}`);
    } catch (error) {
      setSubmitError(error);
      setSubmitting(false);
    }
  }

  const loading = profile.isPending || keywords.isPending || questions.isPending || competitors.isPending;

  return (
    <div className="flex flex-col gap-8 pb-24">
      <div className="flex flex-col gap-3">
        <Eyebrow dot>03 · Confirm</Eyebrow>
        <h1 className="text-display text-foreground">Here&apos;s what we found.</h1>
        <p className="max-w-2xl text-body-regular text-muted-foreground">Check three things, then we&apos;ll run the first analysis. You can change any of this later in Settings.</p>
      </div>
      {loading ? <LoadingBlock rows={4} /> : null}
      {profile.error ? <ErrorState error={profile.error} onRetry={() => profile.refetch()} /> : null}

      {needsChannel && resolve.data ? (
        <Section index={0} title="Which channel is yours?" subtitle="We found a few channels. We only attach one that links to your website or that you pick here.">
          <OwnershipPicker candidates={resolve.data.candidates} selected={channelChoice} onSelect={setChannelChoice} />
        </Section>
      ) : null}

      {profile.data ? (
        <Section index={1} title="Is this your business?" subtitle="Every fact links to where we read it. Hover a fact to see the exact quote.">
          <ProfileReview view={profile.data} />
        </Section>
      ) : null}

      {keywords.data && questions.data ? (
        <Section index={2} title="What do your buyers ask?" subtitle="We'll match videos to these. Untick anything that isn't yours.">
          <TopicReview label="Buyer questions" options={questions.data.items} selected={questionSelection} onToggle={(id) => setSelectedQuestions(toggle(questionSelection, id))} />
          <TopicReview label="Keywords" options={keywords.data.items} selected={keywordSelection} onToggle={(id) => setSelectedKeywords(toggle(keywordSelection, id))} />
        </Section>
      ) : null}

      {competitors.data ? (
        <Section index={3} title="Who do you compete with?" subtitle={`${confirmedCount} selected · need ${MIN_COMPETITORS}–${MAX_COMPETITORS}. Website match and YouTube match are shown separately.`}>
          <CompetitorReview competitors={reviewable} decisions={competitorDecisions} onDecide={(id, decision) => setDecisions({ ...competitorDecisions, [id]: decision })} />
          {excluded.map((item) => (
            <InfoNote key={item.id}>
              Left out {item.name}: {item.evidence_summary.toLowerCase()}.
            </InfoNote>
          ))}
          {alsoWatch.length ? (
            <details className="rounded-card bg-card p-4 shadow-card">
              <summary className="cursor-pointer text-body-medium">Also watch these (optional) · {alsoWatch.length}</summary>
              <ul className="mt-3 flex flex-col gap-2">
                {alsoWatch.map((item) => (
                  <li key={item.id} className="text-body-regular text-foreground">
                    {item.name} <span className="text-muted-foreground">· {RELATION_LABELS[item.relation]} · {item.evidence_summary}</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </Section>
      ) : null}

      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1280px] flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <p className="text-body-regular text-muted-foreground" role="status">
            {blocker ?? `${confirmedCount} competitors · ${topicCount} topics. The first analysis takes about two minutes.`}
          </p>
          <Button variant="primary" size="medium" trailingIcon={RiArrowRightLine} disabled={Boolean(blocker) || submitting || loading} onClick={startAnalysis}>
            {submitting ? "Starting…" : "Start analysis"}
          </Button>
        </div>
        {submitError ? (
          <div className="mx-auto max-w-[1280px] px-4 pb-3 sm:px-8">
            <ErrorState error={submitError} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
