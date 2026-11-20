import type { GrowthHackerApi, TopicApi } from "../contract";
import { ApiError } from "../errors";
import type { BuyerQuestionItem, Client, CompetitorView, KeywordItem, Page, PipelineRun } from "../types";
import { channels, CLIENT_ID, daysAgo, envelope, NOW } from "./fixtures/base";
import { competitorDetail, competitorViews, scorecards } from "./fixtures/competitors";
import {
  creators,
  newsOpportunities,
  outliers,
  ownPerformance,
  recommendations,
  refreshSuggestions,
  trending,
  whiteSpace,
} from "./fixtures/insights";
import { profileView } from "./fixtures/profile";
import { buyerQuestions, keywords } from "./fixtures/topics";
import { getMockRun, listMockRuns, mockEventsAfter, startMockRun } from "./runs";

const LATENCY_MS = 220;

function respond<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(structuredClone(value)), LATENCY_MS));
}

function page<T>(items: T[]): Page<T> {
  return { items, next_cursor: null, limit: 25, total: items.length };
}

const apollo: Client = {
  id: CLIENT_ID,
  name: "Apollo Hospitals",
  website_url: "https://www.apollohospitals.com",
  own_channel: channels.apollo,
  lifecycle: "active",
  window_days: 30,
  market: { country: "IN", language: "en-IN", timezone: "Asia/Kolkata" },
  refresh_hour_local: 6,
  paused: false,
  oauth_connected: false,
  active_run_id: null,
  last_success_at: NOW,
  created_at: daysAgo(12),
  updated_at: NOW,
  version: 3,
};

const clients = new Map<string, Client>([[apollo.id, apollo]]);
const competitorState = new Map(competitorViews.map((item) => [item.id, item]));
const keywordState = new Map(keywords.map((item) => [item.id, item]));
const questionState = new Map(buyerQuestions.map((item) => [item.id, item]));

startMockRun(CLIENT_ID, "onboarding", 30, Date.parse(daysAgo(12)));
startMockRun(CLIENT_ID, "analysis", 30, Date.parse(daysAgo(12)) + 600_000);
startMockRun(CLIENT_ID, "analysis", 30, Date.parse(daysAgo(0.2)));

function requireClient(clientId: string): Client {
  const client = clients.get(clientId);
  if (!client) throw new ApiError(404, "not_found", "We couldn't find that client.");
  return client;
}

function updateCompetitor(competitorId: string, patch: Partial<CompetitorView>): CompetitorView {
  const current = competitorState.get(competitorId);
  if (!current) throw new ApiError(404, "not_found", "We couldn't find that competitor.");
  const next = { ...current, ...patch };
  competitorState.set(competitorId, next);
  return next;
}

function topicApi<T extends KeywordItem | BuyerQuestionItem>(state: Map<string, T>, prefix: string): TopicApi<T> {
  return {
    list: (_clientId, status) => respond(page([...state.values()].filter((item) => !status || item.status === status))),
    create: (_clientId, input) => {
      const item = { ...[...state.values()][0], id: `${prefix}_${Date.now()}`, text: input.text, status: input.status ?? "active", source: "user", priority: input.priority ?? 3, created_at: new Date().toISOString() } as T;
      state.set(item.id, item);
      return respond(item);
    },
    update: (_clientId, id, patch) => {
      const current = state.get(id);
      if (!current) throw new ApiError(404, "not_found", "We couldn't find that topic.");
      const next = { ...current, ...patch } as T;
      state.set(id, next);
      return respond(next);
    },
    remove: (_clientId, id) => {
      state.delete(id);
      return respond(undefined);
    },
    bulkStatus: (_clientId, input) => {
      input.ids.forEach((id) => {
        const current = state.get(id);
        if (current) state.set(id, { ...current, status: input.status });
      });
      return respond({ updated: input.ids.length });
    },
  };
}

function requireRunFor(clientId: string, kind: PipelineRun["kind"]): PipelineRun {
  const client = requireClient(clientId);
  const run = startMockRun(clientId, kind, client.window_days);
  clients.set(clientId, { ...client, active_run_id: run.id, lifecycle: kind === "onboarding" ? "onboarding" : "analyzing" });
  return run;
}

export const mockApi: GrowthHackerApi = {
  clients: {
    list: () => respond(page([...clients.values()])),
    create: (input) => {
      const id = `${CLIENT_ID}_${(clients.size + 1).toString()}`;
      const client: Client = {
        ...apollo,
        id,
        name: input.name,
        website_url: input.website_url ?? null,
        own_channel: null,
        lifecycle: "onboarding",
        market: input.market,
        window_days: input.window_days ?? 30,
        last_success_at: null,
        created_at: new Date().toISOString(),
        version: 1,
      };
      clients.set(id, client);
      const onboarding_run = requireRunFor(id, "onboarding");
      return respond({ ...requireClient(id), onboarding_run });
    },
    get: (clientId) => respond(requireClient(clientId)),
    update: (clientId, patch) => {
      const next = { ...requireClient(clientId), ...patch, version: requireClient(clientId).version + 1 };
      clients.set(clientId, next);
      return respond(next);
    },
    remove: (clientId) => {
      clients.delete(clientId);
      return respond({ status: "deleting" as const });
    },
    resolveChannel: () =>
      respond({
        candidates: [
          { channel: channels.apollo, ownership_evidence: ["Linked from apollohospitals.com footer", "Channel links back to apollohospitals.com"], confidence: "high" as const },
          { channel: channels.apollo247, ownership_evidence: ["Same group name, different website (apollo247.com)"], confidence: "low" as const },
        ],
      }),
    restartOnboarding: (clientId) => respond(requireRunFor(clientId, "onboarding")),
    confirmSetup: (clientId) => {
      const run = requireRunFor(clientId, "analysis");
      const confirmed = [...competitorState.values()].filter((item) => item.status === "confirmed").length;
      return respond({
        run,
        validation: {
          competitors_confirmed: confirmed,
          keywords_active: [...keywordState.values()].filter((item) => item.status === "active").length,
          buyer_questions_active: [...questionState.values()].filter((item) => item.status === "active").length,
        },
      });
    },
  },
  profile: {
    get: () => respond(profileView),
    update: (_clientId, patch) => respond({ ...profileView, overrides: patch, version: profileView.version + 1 }),
    recrawl: (clientId) => respond(startMockRun(clientId, "recrawl", 30)),
  },
  keywords: topicApi(keywordState, "kw"),
  buyerQuestions: topicApi(questionState, "bq"),
  competitors: {
    list: (_clientId, status) => respond(page([...competitorState.values()].filter((item) => !status || item.status === status))),
    add: (_clientId, input) => {
      const handle = input.channel_ref.startsWith("@") ? input.channel_ref : `@${input.channel_ref}`;
      const added: CompetitorView = {
        ...competitorViews[0],
        id: `cmp_user_${Date.now()}`,
        status: "confirmed",
        name: input.name ?? handle,
        channel: { ...channels.fortis, channel_id: `UCuser${Date.now()}`, title: input.name ?? handle, handle, subscriber_count: null },
        added_by: "user",
        source: "user",
        evidence_summary: "Added by you",
        preview_video_ids: [],
      };
      competitorState.set(added.id, added);
      return respond(added);
    },
    suggest: (clientId) => respond(startMockRun(clientId, "onboarding", 30)),
    confirm: (_clientId, competitorId) => {
      const confirmed = [...competitorState.values()].filter((item) => item.status === "confirmed").length;
      if (confirmed >= 10) throw new ApiError(422, "competitors_out_of_range", "You can track up to 10. Move extras to 'Also watch'.");
      return respond(updateCompetitor(competitorId, { status: "confirmed", reject_reason: null }));
    },
    reject: (_clientId, competitorId, input) =>
      respond(updateCompetitor(competitorId, { status: "rejected", reject_reason: input.reason ?? "other" })),
    remove: (_clientId, competitorId) => {
      competitorState.delete(competitorId);
      return respond(undefined);
    },
  },
  runs: {
    create: (clientId, input) => respond(requireRunFor(clientId, input.kind)),
    list: (clientId) => respond(page(listMockRuns(clientId))),
    get: (runId) => respond(getMockRun(runId)),
    cancel: (runId) => respond({ ...getMockRun(runId), status: "cancelled" as const }),
    retry: (runId) => {
      const original = getMockRun(runId);
      return respond(startMockRun(original.client_id, original.kind, original.window_days));
    },
    events: (runId, afterSeq) => {
      const items = mockEventsAfter(runId, afterSeq);
      return respond({ items, last_seq: items.at(-1)?.seq ?? afterSeq, run_status: getMockRun(runId).status });
    },
    streamToken: () => respond({ token: "mock", expires_at: new Date(Date.now() + 600_000).toISOString() }),
  },
  analytics: {
    ownPerformance: (_clientId, windowDays) => respond(envelope(ownPerformance, windowDays)),
    competitors: (_clientId, windowDays) => respond(envelope(scorecards, windowDays)),
    competitor: (_clientId, channelId, windowDays) => {
      const detail = competitorDetail(channelId);
      if (!detail) throw new ApiError(404, "not_found", "We couldn't find that competitor.");
      return respond(envelope(detail, windowDays));
    },
    competitorVideos: (_clientId, channelId) =>
      respond(page(competitorDetail(channelId)?.top_outliers ?? [])),
    trending: (_clientId, windowDays) => respond(envelope(trending, windowDays)),
    outliers: (_clientId, query) => {
      const scoped = query.scope === "own" ? [] : outliers;
      return respond(envelope(page(scoped), query.window_days));
    },
    patterns: (_clientId, _scope, windowDays) => respond(envelope(competitorDetail(channels.fortis.channel_id)?.trait_lift ?? [], windowDays)),
    topics: (_clientId, windowDays) =>
      respond(envelope(buyerQuestions.map((item, index) => ({ topic_ref: { id: item.id, label: item.text }, own_videos: index % 3, competitor_videos: 6 - index, outliers: index % 2, review_flagged: false })), windowDays)),
  },
  recommendations: {
    list: (_clientId, query) => {
      const windowDays = query.window_days ?? 30;
      const items = recommendations.filter((item) => (!query.type || item.type === query.type) && (!query.lens || item.lens.includes(query.lens)));
      const thin = windowDays === 7;
      return respond({
        state: "ready" as const,
        envelope: {
          ...envelope(page(thin ? items.slice(0, 1) : items.map((item) => ({ ...item, window_days: windowDays }))), windowDays),
          notices: thin ? [{ code: "thin_data", level: "info" as const, message: "Not enough outliers in the last 7 days (2 found across 1 channel; we need 3+).", suggest: { widen_window_to: 30 as const } }] : [],
          white_space: whiteSpace,
        },
      });
    },
    get: (recommendationId) => {
      const found = recommendations.find((item) => item.id === recommendationId);
      if (!found) throw new ApiError(404, "not_found", "We couldn't find that recommendation.");
      return respond(found);
    },
    feedback: (_recommendationId, input) => respond({ feedback: input }),
  },
  news: {
    opportunities: () => respond(envelope(page(newsOpportunities), 30)),
    signals: () => respond(page(newsOpportunities.map((item) => item.news))),
    dismiss: (opportunityId) => {
      const found = newsOpportunities.find((item) => item.id === opportunityId);
      if (!found) throw new ApiError(404, "not_found", "We couldn't find that opportunity.");
      return respond({ ...found, status: "dismissed" as const });
    },
  },
  refresh: {
    list: () => respond(envelope(page(refreshSuggestions), 30)),
    applied: (suggestionId) => {
      const found = refreshSuggestions.find((item) => item.id === suggestionId);
      if (!found) throw new ApiError(404, "not_found", "We couldn't find that suggestion.");
      return respond({ ...found, status: "applied" as const, applied_at: new Date().toISOString() });
    },
    dismiss: (suggestionId) => {
      const found = refreshSuggestions.find((item) => item.id === suggestionId);
      if (!found) throw new ApiError(404, "not_found", "We couldn't find that suggestion.");
      return respond({ ...found, status: "dismissed" as const });
    },
  },
  creators: {
    list: (_clientId, windowDays) => respond(envelope(page(creators), windowDays)),
  },
  integrations: {
    youtubeConnect: () => respond({ authorization_url: "#oauth-not-available-in-mock-mode" }),
    youtubeDisconnect: () => respond(undefined),
  },
  evaluation: {
    startBacktest: (input) => respond(startMockRun(input.client_id, "analysis", input.window_days)),
    outlierLabels: () => respond(page([])),
    putOutlierLabel: (videoId, label) => respond({ video_id: videoId, ...label }),
    outlierPrecision: () => respond({ precision: 0.84, recall: 0.71, n: 120, threshold: 3 }),
    submitReview: () => respond(undefined),
  },
  ops: {
    quota: () =>
      respond({
        youtube: { pt_day: NOW.slice(0, 10), units_used: 2_140, units_limit: 10_000, search_calls_used: 31, search_calls_limit: 100, reset_at: NOW },
        llm: { usd_today: 1.84 },
        tavily: { credits_today: 46 },
      }),
  },
};
