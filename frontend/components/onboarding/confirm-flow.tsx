"use client";

import { useQuery } from "@tanstack/react-query";
import { RiArrowDownLine, RiArrowUpLine } from "@remixicon/react";
import { AnimatePresence, LazyMotion, MotionConfig, domAnimation, m } from "motion/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState, type ReactNode } from "react";
import { ErrorState, InfoNote, LoadingBlock } from "@/components/states/states";
import { LoadingState } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { buyerQuestionsQuery, clientKey, clientQuery, competitorsQuery, keywordsQuery, profileQuery } from "@/lib/api/queries";
import type { BuyerQuestionItem, ChannelVerifiedBy, CompetitorView, KeywordItem, ProfileFact, ProfileGroup } from "@/lib/api/types";
import { channelFacts, primaryChannel } from "@/lib/competitors";
import { isMeaningfulContact } from "@/lib/contact";
import { SOURCE_KIND_LABELS } from "@/lib/labels";
import { activeTopics, checkFirst, competitorBlocker, competitorCheckReason, factCheckReason, groupCompetitors, keywordCheckReason, newItemId, questionCheckReason, untracked } from "@/lib/onboarding/edits";
import { flowReducer, keyIntent, resolveScreen, screensFor, type FlowAction, type FlowState, type ScreenId } from "@/lib/onboarding/flow";
import { marketLine } from "@/lib/onboarding/market";
import { useCompetitorEdits, useProfileEdits, useTopicEdits } from "@/lib/onboarding/use-confirm-edits";
import { useClientId } from "@/lib/use-client-id";
import { EditableList, type ListItem } from "./editable-list";
import { FlowArrows, FlowProgress, FlowScreen } from "./flow-frame";
import { OwnershipPicker } from "./ownership-picker";
import { SummaryEditor } from "./summary-editor";

const VERIFIED_BY_NOTE: Record<ChannelVerifiedBy, string> = {
  website_link: "your website links to it",
  channel_link: "it links to your website",
  oauth: "you signed in with it",
  manual: "you picked it",
};

const FACT_SCREENS: Record<"offerings" | "locations" | "contacts" | "reasons", { group: ProfileGroup; title: string; helper: string; noun: string; placeholder: string }> = {
  offerings: { group: "offerings", title: "What do you sell?", helper: "We match videos to these. Click a line to correct it, × to remove it.", noun: "product or service", placeholder: "Add a product or service" },
  locations: { group: "locations", title: "Where do you work?", helper: "Branches, offices and the areas you serve. Remove anything that isn't yours.", noun: "location", placeholder: "Add a city, branch or area you serve" },
  contacts: { group: "conversion_actions", title: "How do customers reach you?", helper: "Calls, forms and messages we found on your site. Videos will point people here.", noun: "way to reach you", placeholder: "Add a phone number, email or link" },
  reasons: { group: "value_propositions", title: "Why do customers choose you?", helper: "What makes you different, in your own words. We use these to angle video ideas.", noun: "reason", placeholder: "Add a reason customers pick you" },
};

const SCREEN_EYEBROW: Record<string, string> = { business: "1 · Your business", buyers: "2 · Your buyers", competitors: "3 · Your competitors", review: "Last step" };

const SOURCE_TONE: Record<string, string> = { site: "bg-info", third: "bg-warning", user: "bg-foreground", youtube: "bg-danger" };

// onboarding keeps finding competitors after the review opens
const SETUP_POLL_MS = 3_000;

function storageKey(clientId: string): string {
  return `gh:confirm:${clientId}`;
}

function factItem(fact: ProfileFact): ListItem {
  const evidence = fact.meta.evidence[0];
  const label = evidence ? SOURCE_KIND_LABELS[evidence.source_kind] : null;
  const tone = !evidence || evidence.source_kind === "user" ? "user" : label?.thirdParty ? "third" : evidence.source_kind === "youtube_channel" ? "youtube" : "site";
  return {
    id: fact.id,
    text: fact.label,
    detail: fact.detail && fact.detail !== fact.label ? fact.detail : undefined,
    check: factCheckReason(fact),
    source: { label: label?.label ?? "Added by you", href: evidence?.url || undefined, quote: evidence?.quote, tone: SOURCE_TONE[tone] },
  };
}

function topicItem(item: KeywordItem | BuyerQuestionItem, check: string | null): ListItem {
  const user = item.source === "user";
  return { id: item.id, text: item.text, check, source: { label: user ? "Added by you" : check ? "Suggested" : "From your site", tone: SOURCE_TONE[user ? "user" : check ? "third" : "site"] } };
}

function competitorItem(item: CompetitorView): ListItem {
  const primary = primaryChannel(item);
  const facts = primary ? channelFacts(primary).slice(0, 2).join(" · ") : "no YouTube channel found";
  return {
    id: item.id,
    text: item.name,
    detail: [primary?.channel.handle, facts].filter(Boolean).join(" · "),
    check: competitorCheckReason(item),
    source: { label: item.added_by === "user" ? "Added by you" : item.evidence_summary || "Suggested", tone: SOURCE_TONE[item.added_by === "user" ? "user" : "site"] },
  };
}

// generic on purpose: the regulated flag can be set for any field, not just healthcare
const REGULATED_NOTICE = "This is a regulated field, so news ideas and description changes will ask for review before publishing.";

export function ConfirmFlow() {
  const clientId = useClientId();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const client = useQuery({ ...clientQuery(clientId), refetchInterval: (query) => (query.state.data?.lifecycle === "onboarding" ? SETUP_POLL_MS : false) });
  const setupPending = client.data?.lifecycle === "onboarding";
  const profile = useQuery(profileQuery(clientId));
  const keywords = useQuery(keywordsQuery(clientId));
  const questions = useQuery(buyerQuestionsQuery(clientId));
  const competitors = useQuery({ ...competitorsQuery(clientId), refetchInterval: setupPending ? SETUP_POLL_MS : false });
  const refetchCompetitors = competitors.refetch;
  const wasPending = useRef(setupPending);
  const ownChannel = client.data?.own_channel ?? null;
  const needsChannel = client.data ? !ownChannel?.verified : false;
  const channelRef = ownChannel?.channel_id ?? client.data?.website_url ?? client.data?.name ?? "";
  const resolve = useQuery({
    queryKey: [...clientKey(clientId), "channel-resolve", channelRef],
    queryFn: () => api.clients.resolveChannel(clientId, channelRef),
    enabled: needsChannel && Boolean(channelRef),
    staleTime: Infinity,
  });
  const askChannel = needsChannel && Boolean(resolve.data?.candidates.length);
  const screens = useMemo(() => screensFor({ askChannel }), [askChannel]);

  const saveProfile = useProfileEdits(clientId);
  const keywordEdits = useTopicEdits(clientId, "keywords");
  const questionEdits = useTopicEdits(clientId, "buyer-questions");
  const competitorEdits = useCompetitorEdits(clientId);

  const loading = client.isPending || profile.isPending || keywords.isPending || questions.isPending || competitors.isPending || (needsChannel && resolve.isPending);
  const [flow, setFlow] = useState<FlowState | null>(null);
  const [channelChoice, setChannelChoice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const stepParam = params.get("step");
  // removed facts are kept so "undo" can send the whole fact back
  const removedFacts = useRef(new Map<string, ProfileFact>()).current;

  // resume where the user left: the url first, then the last screen saved for this client
  const current: FlowState = useMemo(() => flow ?? { screen: resolveScreen(screens, stepParam, window.localStorage.getItem(storageKey(clientId))), direction: 1 }, [clientId, flow, screens, stepParam]);

  useEffect(() => {
    if (loading) return;
    window.localStorage.setItem(storageKey(clientId), current.screen);
    if (stepParam !== current.screen) {
      const next = new URLSearchParams(params.toString());
      next.set("step", current.screen);
      router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    }
  }, [clientId, current.screen, loading, params, pathname, router, stepParam]);

  // one last read once onboarding hands over, in case discovery finished between two polls
  useEffect(() => {
    if (wasPending.current && !setupPending) void refetchCompetitors();
    wasPending.current = setupPending;
  }, [setupPending, refetchCompetitors]);

  const dispatch = useCallback(
    (action: FlowAction) => {
      setFlow((state) => flowReducer(screens, state ?? current, action));
      window.scrollTo({ top: 0 });
    },
    [current, screens],
  );

  const view = profile.data;
  const facts = (key: keyof typeof FACT_SCREENS): ProfileFact[] => {
    const list = view?.effective[FACT_SCREENS[key].group] ?? [];
    return key === "contacts" ? list.filter((fact) => isMeaningfulContact(fact.label)) : list;
  };
  const keywordItems = activeTopics(keywords.data?.items ?? []);
  const questionItems = activeTopics(questions.data?.items ?? []);
  const groups = groupCompetitors(competitors.data?.items ?? []);
  const topicCount = keywordItems.length + questionItems.length;
  const discovering = setupPending && !competitors.data?.items.length;

  const blockerFor = (screen: ScreenId): string | null => {
    if (discovering && (screen === "direct" || screen === "watch")) return "Still finding competitors.";
    if (setupPending && screen === "review") return "Finishing your setup. This takes a few more seconds.";
    if (screen === "keywords" && topicCount === 0) return "Keep at least one buyer question or keyword.";
    if (screen === "direct" || screen === "review") return competitorBlocker(groups.direct.length) ?? (screen === "review" && topicCount === 0 ? "Keep at least one buyer question or keyword." : null);
    return null;
  };

  async function startAnalysis() {
    setBusy(true);
    setSubmitError(null);
    try {
      await Promise.all([
        competitorEdits.confirm(untracked(groups.direct)),
        keywordEdits.confirm(keywordItems.filter((item) => item.status === "proposed").map((item) => item.id)),
        questionEdits.confirm(questionItems.filter((item) => item.status === "proposed").map((item) => item.id)),
      ]);
      const { run } = await api.clients.confirmSetup(clientId, client.data?.window_days, idempotencyKey);
      window.localStorage.removeItem(storageKey(clientId));
      router.push(`/c/${clientId}/setup/analysis?run=${run.id}`);
    } catch (error) {
      setSubmitError(error);
      setBusy(false);
    }
  }

  // "looks right" also confirms what the screen showed, so a re-crawl keeps it
  async function confirmScreen(screen: ScreenId) {
    if (blockerFor(screen)) return;
    if (screen === "review") return startAnalysis();
    if (screen in FACT_SCREENS) {
      const key = screen as keyof typeof FACT_SCREENS;
      const group = FACT_SCREENS[key].group;
      const open = facts(key).filter((fact) => fact.meta.status === "proposed" || fact.meta.status === "needs_confirmation" || fact.meta.status === "stale");
      if (open.length) void saveProfile({ facts: open.map((fact) => ({ group, op: "confirm" as const, id: fact.id })) });
    } else if (screen === "channel" && channelChoice) {
      setBusy(true);
      try {
        await api.clients.attachChannel(clientId, channelChoice);
        await client.refetch();
      } catch (error) {
        setSubmitError(error);
        return;
      } finally {
        setBusy(false);
      }
    } else if (screen === "questions") void questionEdits.confirm(questionItems.filter((item) => item.status === "proposed").map((item) => item.id));
    else if (screen === "keywords") void keywordEdits.confirm(keywordItems.filter((item) => item.status === "proposed").map((item) => item.id));
    else if (screen === "direct") void competitorEdits.confirm(untracked(groups.direct));
    dispatch({ type: "next" });
  }

  const onFlowKey = useEffectEvent((event: KeyboardEvent) => {
    const intent = keyIntent(event, event.target as HTMLElement | null);
    if (!intent || busy) return;
    event.preventDefault();
    if (intent === "back") dispatch({ type: "back" });
    else void confirmScreen(current.screen);
  });

  useEffect(() => {
    if (loading) return;
    window.addEventListener("keydown", onFlowKey);
    return () => window.removeEventListener("keydown", onFlowKey);
  }, [loading]);

  if (profile.error) return <ErrorState error={profile.error} onRetry={() => profile.refetch()} />;
  if (loading || !view) return <LoadingBlock rows={4} />;

  const index = screens.findIndex((screen) => screen.id === current.screen);
  const screen = screens[index];
  const back = index > 0 ? () => dispatch({ type: "back" }) : undefined;
  const frame = (title: string, helper: ReactNode, body: ReactNode, confirmLabel?: string) => (
    <FlowScreen eyebrow={SCREEN_EYEBROW[screen.step]} title={title} helper={helper} onConfirm={() => void confirmScreen(screen.id)} onBack={back} blocker={blockerFor(screen.id)} busy={busy} confirmLabel={confirmLabel}>
      {body}
    </FlowScreen>
  );

  function factScreen(key: keyof typeof FACT_SCREENS) {
    const config = FACT_SCREENS[key];
    const list = checkFirst(facts(key), factCheckReason);
    const byId = new Map(list.map((fact) => [fact.id, fact]));
    return frame(
      config.title,
      config.helper,
      <EditableList
        label={config.title}
        noun={config.noun}
        addPlaceholder={config.placeholder}
        items={list.map(factItem)}
        onEdit={(item, label) => void saveProfile({ facts: [{ group: config.group, op: "edit", id: item.id, label }] })}
        onRemove={(item) => {
          const fact = byId.get(item.id);
          if (fact) removedFacts.set(item.id, fact);
          void saveProfile({ facts: [{ group: config.group, op: "remove", id: item.id }] });
        }}
        onRestore={(item) => {
          const fact = removedFacts.get(item.id);
          if (fact) void saveProfile({ facts: [{ group: config.group, op: "restore", fact }] });
        }}
        onAdd={(label) => {
          void saveProfile({ facts: [{ group: config.group, op: "add", id: newItemId(), label }] });
          return null;
        }}
      />,
    );
  }

  function renderScreen(): ReactNode {
    if (discovering && (screen.id === "direct" || screen.id === "watch")) {
      return frame(
        screen.id === "direct" ? "Who are your direct competitors?" : "Anyone else worth watching?",
        "We're still searching YouTube for businesses like yours. This fills in on its own.",
        <LoadingState label="Finding competitors" showGrid={false} />,
      );
    }
    switch (screen.id) {
      case "summary":
        return frame(
          "Is this what you do?",
          "We wrote this from your website. Click it to correct it in your own words.",
          <div className="flex flex-col gap-4">
            {ownChannel?.verified ? (
              <InfoNote>
                Using your YouTube channel {ownChannel.title ?? ownChannel.channel_id}
                {ownChannel.handle ? ` (${ownChannel.handle})` : ""}
                {ownChannel.verified_by ? `: ${VERIFIED_BY_NOTE[ownChannel.verified_by]}.` : "."}
              </InfoNote>
            ) : null}
            <SummaryEditor view={view!} onSummary={(text) => void saveProfile({ identity: { one_liner: text } })} onCountry={(code) => void saveProfile({ identity: { country: code } })} />
          </div>,
        );
      case "channel":
        return frame(
          "Which YouTube channel is yours?",
          "We only attach a channel that links to your website, or one you pick here.",
          <OwnershipPicker candidates={resolve.data?.candidates ?? []} selected={channelChoice} onSelect={setChannelChoice} />,
          channelChoice ? "This is mine" : "Continue without a channel",
        );
      case "offerings":
      case "locations":
      case "contacts":
      case "reasons":
        return factScreen(screen.id);
      case "questions":
        return frame(
          "What do your buyers ask?",
          "We find videos that answer these. Remove any that don't fit, or add the ones you hear every week.",
          <EditableList
            label="Buyer questions"
            noun="buyer question"
            addPlaceholder="Add a question your buyers ask"
            minLength={5}
            items={checkFirst(questionItems, questionCheckReason).map((item) => topicItem(item, questionCheckReason(item)))}
            onEdit={(item, text) => void questionEdits.edit(item.id, text)}
            onRemove={(item) => void questionEdits.remove(item.id)}
            onRestore={(item) => void questionEdits.restore(item.id)}
            onAdd={(text) => questionEdits.add(text)}
          />,
        );
      case "keywords":
        return frame(
          "Which words describe what you do?",
          "Keywords we read on your site, plus a few we suggest. Suggestions are marked “Check this”.",
          <EditableList
            label="Keywords"
            noun="keyword"
            maxLength={80}
            items={checkFirst(keywordItems, keywordCheckReason).map((item) => topicItem(item, keywordCheckReason(item)))}
            onEdit={(item, text) => void keywordEdits.edit(item.id, text)}
            onRemove={(item) => void keywordEdits.remove(item.id)}
            onRestore={(item) => void keywordEdits.restore(item.id)}
            onAdd={(text) => keywordEdits.add(text)}
          />,
        );
      case "direct":
        return frame(
          "Who are your direct competitors?",
          <>
            Businesses that sell what you sell, with a YouTube channel. <span className="font-mono text-foreground">{groups.direct.length}</span> selected · keep 3–10.
          </>,
          <CompetitorSegment items={groups.direct} role="competitor_set" edits={competitorEdits} />,
        );
      case "watch":
        return frame(
          "Anyone else worth watching?",
          "Optional. Channels in your niche or ones you look up to. We learn from their topics; they don't count as competitors.",
          <div className="flex flex-col gap-3">
            <CompetitorSegment items={groups.watch} role="niche_set" edits={competitorEdits} />
            {groups.noChannel.length ? (
              <p className="text-caption-1-regular text-muted-foreground">
                No YouTube channel found for {groups.noChannel.map((item) => item.name).join(", ")}. Add their channel link above if they have one.
              </p>
            ) : null}
          </div>,
          groups.watch.length ? "Looks right" : "Skip",
        );
      case "review":
        return frame(
          "Ready for your first analysis?",
          "Here's what we'll use. It takes about two minutes, and you can change any of this later in Settings.",
          <ReviewSummary
            rows={[
              { label: "Business", value: view!.effective.identity.one_liner, screen: "summary" },
              { label: "Market", value: marketLine(view!.effective.identity.country, view!.effective.locations), screen: "summary" },
              { label: "Offerings", value: `${facts("offerings").length}`, screen: "offerings" },
              { label: "Locations", value: `${facts("locations").length}`, screen: "locations" },
              { label: "Buyer questions", value: `${questionItems.length}`, screen: "questions" },
              { label: "Keywords", value: `${keywordItems.length}`, screen: "keywords" },
              { label: "Direct competitors", value: groups.direct.map((item) => item.name).join(", ") || "None yet", screen: "direct" },
              { label: "Also watching", value: groups.watch.map((item) => item.name).join(", ") || "None", screen: "watch" },
            ]}
            onEdit={(target) => dispatch({ type: "go", screen: target })}
            notice={view!.effective.freshness.regulated_domain ? REGULATED_NOTICE : null}
            error={submitError}
          />,
          busy ? "Starting…" : "Start analysis",
        );
    }
  }

  return (
    <MotionConfig reducedMotion="user">
      <LazyMotion features={domAnimation} strict>
        <div className="flex flex-col pb-20">
          <FlowProgress screens={screens} current={current.screen} onJump={(target) => dispatch({ type: "go", screen: target })} />
          <div className="relative mx-auto w-full max-w-2xl pt-8 sm:pt-14">
            <AnimatePresence mode="wait" initial={false} custom={current.direction}>
              <m.section
                key={screen.id}
                custom={current.direction}
                variants={{
                  enter: (direction: number) => ({ opacity: 0, y: direction * 56 }),
                  center: { opacity: 1, y: 0 },
                  exit: (direction: number) => ({ opacity: 0, y: direction * -56 }),
                }}
                initial="enter"
                animate="center"
                exit="exit"
                transition={{ duration: 0.32, ease: [0.2, 0.8, 0.2, 1] }}
              >
                {renderScreen()}
              </m.section>
            </AnimatePresence>
          </div>
          <FlowArrows onBack={() => dispatch({ type: "back" })} onNext={() => void confirmScreen(screen.id)} canBack={index > 0} canNext={!blockerFor(screen.id) && screen.id !== "review"} />
        </div>
      </LazyMotion>
    </MotionConfig>
  );
}

type CompetitorEdits = ReturnType<typeof useCompetitorEdits>;

function CompetitorSegment({ items, role, edits }: { items: CompetitorView[]; role: "competitor_set" | "niche_set"; edits: CompetitorEdits }) {
  const byId = new Map(items.map((item) => [item.id, item]));
  const [moved] = useState(() => new Map<string, CompetitorView>());
  const direct = role === "competitor_set";
  return (
    <EditableList
      label={direct ? "Direct competitors" : "Also watch"}
      noun={direct ? "competitor" : "channel to watch"}
      addPlaceholder="Add by name, website or YouTube channel link"
      items={checkFirst(items, competitorCheckReason).map(competitorItem)}
      onRemove={(item) => {
        const competitor = byId.get(item.id);
        if (competitor) {
          moved.set(item.id, competitor);
          void edits.remove(competitor);
        }
      }}
      onRestore={(item) => {
        const competitor = moved.get(item.id);
        if (competitor) void edits.restore(competitor);
      }}
      onAdd={(text) => edits.add(text, role)}
      actions={(item) => {
        const competitor = byId.get(item.id);
        if (!competitor) return null;
        const Icon = direct ? RiArrowDownLine : RiArrowUpLine;
        const label = direct ? "Move to also watch" : "Move to direct competitors";
        return (
          <button
            type="button"
            onClick={() => void edits.move(competitor, direct ? "niche_set" : "competitor_set")}
            aria-label={`${label}: ${item.text}`}
            title={label}
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-control px-2 text-caption-1-regular text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <Icon aria-hidden className="size-3.5" />
            <span className="hidden sm:inline">{direct ? "Also watch" : "Direct"}</span>
          </button>
        );
      }}
      empty={direct ? "No direct competitors yet. Add 3 you know below." : "Nothing here. Add a channel you learn from, or skip."}
    />
  );
}

function ReviewSummary({ rows, onEdit, notice, error }: { rows: { label: string; value: string; screen: ScreenId }[]; onEdit: (screen: ScreenId) => void; notice: string | null; error: unknown }) {
  return (
    <div className="flex flex-col gap-4">
      <dl className="divide-y divide-border overflow-hidden rounded-card bg-card shadow-card">
        {rows.map((row) => (
          <div key={row.label} className="flex items-start gap-4 px-4 py-3">
            <dt className="w-36 shrink-0 text-eyebrow text-muted-foreground">{row.label}</dt>
            <dd className={/^\d+$/.test(row.value) ? "flex-1 font-mono text-body-regular text-foreground" : "flex-1 text-body-regular text-foreground"}>{row.value}</dd>
            <button type="button" onClick={() => onEdit(row.screen)} className="shrink-0 text-caption-1-regular text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
              Edit
            </button>
          </div>
        ))}
      </dl>
      {notice ? <InfoNote>{notice}</InfoNote> : null}
      {error ? <ErrorState error={error} /> : null}
    </div>
  );
}
