import type { GrowthHackerApi, TopicApi } from "../contract";
import { getApiKey } from "../credentials";
import { ApiError } from "../errors";
import type {
  BuyerQuestionItem,
  Client,
  CompetitorView,
  FeedbackIn,
  KeywordItem,
  BuyerQuestionCreate,
  KeywordCreate,
  LlmProviderOut,
  LlmSettings,
  Me,
  NewsOpportunityView,
  NotificationSettings,
  Page,
  PipelineRun,
  RecommendationOut,
  RefreshSuggestionView,
} from "../types";
import { channels, CLIENT_ID, daysAgo, envelope, NOW } from "./fixtures/base";
import { answerCitations } from "./fixtures/citations";
import { competitorDetail, competitorViews, gapMatrix, scorecards } from "./fixtures/competitors";
import {
  creators,
  earlySignals,
  newsOpportunities,
  outliers,
  ownPerformance,
  recommendations,
  refreshSuggestions,
  trending,
  whiteSpace,
} from "./fixtures/insights";
import { applyProfileView } from "@/lib/onboarding/edits";
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

let profileState = profileView;
const feedbackState = new Map<string, FeedbackIn>();
const newsState = new Map<string, NewsOpportunityView>(newsOpportunities.map((item) => [item.id, item]));
const refreshState = new Map<string, RefreshSuggestionView>(refreshSuggestions.map((item) => [item.id, item]));

const WORKSPACES: Me["workspaces"] = [
  { id: "ws_demo", name: "Demo agency", slug: "demo", role: "owner" },
  { id: "ws_apollo", name: "Apollo in-house", slug: "apollo-inhouse", role: "viewer" },
];
let activeWorkspaceId = WORKSPACES[0].id;
const SIGNED_OUT_KEY = "gh:mock:signed-out";

function signedOut(): boolean {
  return typeof sessionStorage !== "undefined" && sessionStorage.getItem(SIGNED_OUT_KEY) === "1";
}

function setSignedOut(value: boolean) {
  if (typeof sessionStorage === "undefined") return;
  if (value) sessionStorage.setItem(SIGNED_OUT_KEY, "1");
  else sessionStorage.removeItem(SIGNED_OUT_KEY);
}

const MOCK_TIERS: Record<"anthropic" | "openai_compatible", LlmProviderOut["tiers"]> = {
  anthropic: {
    low: { model: "claude-haiku-4-5", max_tokens: 2048, timeout_s: 20, max_retries: 1, effort: null },
    medium: { model: "claude-sonnet-5-5", max_tokens: 4096, timeout_s: 35, max_retries: 1, effort: "low" },
    high: { model: "claude-sonnet-5-5", max_tokens: 8192, timeout_s: 60, max_retries: 1, effort: "medium" },
  },
  openai_compatible: {
    low: { model: null, max_tokens: 4096, timeout_s: 25, max_retries: 1, effort: null },
    medium: { model: null, max_tokens: 4096, timeout_s: 35, max_retries: 1, effort: null },
    high: { model: null, max_tokens: 8192, timeout_s: 60, max_retries: 1, effort: null },
  },
};

function provider(kind: LlmProviderOut["kind"], baseUrl: string | null, keySet: boolean): LlmProviderOut {
  const openai = kind === "openai_compatible" ? { extra_params: {}, extra_body: {}, structured_dialect: "openai_json_schema_strict" as const, token_param: "max_completion_tokens" as const, supports_reasoning_effort: false } : null;
  return { kind, enabled: true, base_url: baseUrl, tiers: MOCK_TIERS[kind], max_concurrency: kind === "anthropic" ? 8 : 4, connect_timeout_s: 5, openai, api_key_set: keySet, api_key_source: keySet ? "environment" : null, configured: keySet && (kind === "anthropic" || Boolean(baseUrl)), overridden: [] };
}

let llmSettings: LlmSettings = {
  enabled: true,
  provider_order: ["anthropic", "openai_compat"],
  providers: { anthropic: provider("anthropic", "https://api.anthropic.com", true), openai_compat: provider("openai_compatible", null, false) },
  updated_at: null,
};
const notificationState = new Map<string, NotificationSettings>();

function withFeedback(rec: RecommendationOut): RecommendationOut {
  const feedback = feedbackState.get(rec.id);
  return feedback ? { ...rec, feedback: { status: feedback.status ?? null, reason: feedback.reason ?? null, linked_video_id: feedback.linked_video_id ?? null } } : rec;
}

startMockRun(CLIENT_ID, "onboarding", 30, Date.parse(daysAgo(12)));
startMockRun(CLIENT_ID, "analysis", 30, Date.parse(daysAgo(12)) + 600_000);
startMockRun(CLIENT_ID, "analysis", 30, Date.parse(daysAgo(0.2)));

// clients added in this tab are rebuilt from the demo client after a reload, the way runs are rebuilt from their ids
function requireClient(clientId: string): Client {
  const client = clients.get(clientId) ?? (clientId.startsWith(`${CLIENT_ID}_`) ? { ...apollo, id: clientId } : undefined);
  if (!client) throw new ApiError(404, "not_found", "We couldn't find that client.");
  clients.set(clientId, client);
  return client;
}

function updateCompetitor(competitorId: string, patch: Partial<CompetitorView>): CompetitorView {
  const current = competitorState.get(competitorId);
  if (!current) throw new ApiError(404, "not_found", "We couldn't find that competitor.");
  const next = { ...current, ...patch };
  competitorState.set(competitorId, next);
  return next;
}

function topicApi<T extends KeywordItem | BuyerQuestionItem, C extends KeywordCreate | BuyerQuestionCreate>(state: Map<string, T>, prefix: string): TopicApi<T, C> {
  return {
    list: (_clientId, status) => respond(page([...state.values()].filter((item) => !status || item.status === status))),
    create: (_clientId, input) => {
      // created topics start confirmed, as the backend does for user-added ones
      const item = { ...[...state.values()][0], ...input, id: `${prefix}_${Date.now()}`, text: input.text, status: "confirmed", source: "user", created_at: new Date().toISOString(), updated_at: new Date().toISOString() } as T;
      state.set(item.id, item);
      return respond(item);
    },
    update: (_clientId, id, patch) => {
      const current = state.get(id);
      if (!current) throw new ApiError(404, "not_found", "We couldn't find that topic.");
      const next = { ...current, ...Object.fromEntries(Object.entries(patch).filter(([, value]) => value !== null && value !== undefined)) } as T;
      state.set(id, next);
      return respond(next);
    },
    // like the api, a removed topic stays as rejected so it can be restored
    remove: (_clientId, id) => {
      const current = state.get(id);
      if (current) state.set(id, { ...current, status: "rejected" });
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
  auth: {
    login: (input) => {
      if (!input.email.includes("@") || !input.password) throw new ApiError(401, "invalid_credentials", "That email and password don't match.");
      setSignedOut(false);
      return respond({ status: "signed_in" });
    },
    logout: () => {
      setSignedOut(true);
      return respond(undefined);
    },
    me: () => {
      if (signedOut() && !getApiKey()) return Promise.reject(new ApiError(401, "unauthenticated", "Please sign in again."));
      return respond({
        principal: getApiKey() ? ("api_key" as const) : ("user" as const),
        user: getApiKey() ? null : { id: "usr_demo", email: "priya@demo-agency.in", display_name: "Priya" },
        workspaces: WORKSPACES,
        active_workspace_id: activeWorkspaceId,
        role: WORKSPACES.find((item) => item.id === activeWorkspaceId)?.role ?? "owner",
      });
    },
    switchWorkspace: (workspaceId) => {
      if (!WORKSPACES.some((item) => item.id === workspaceId)) throw new ApiError(404, "not_found", "We couldn't find that workspace.");
      activeWorkspaceId = workspaceId;
      return respond({ status: "switched" });
    },
  },
  clients: {
    list: () => respond(page(activeWorkspaceId === WORKSPACES[0].id ? [...clients.values()] : [])),
    create: (input) => {
      const id = `${CLIENT_ID}_${(clients.size + 1).toString()}`;
      const client: Client = {
        ...apollo,
        id,
        name: input.name,
        website_url: input.website_url ?? null,
        own_channel: null,
        lifecycle: "onboarding",
        market: input.market ?? apollo.market,
        window_days: input.window_days ?? 30,
        last_success_at: null,
        created_at: new Date().toISOString(),
        version: 1,
      };
      clients.set(id, client);
      const onboarding_run = requireRunFor(id, "onboarding");
      return respond({ client: requireClient(id), onboarding_run });
    },
    get: (clientId) => respond(requireClient(clientId)),
    update: (clientId, patch) => {
      const next = { ...requireClient(clientId), ...patch, version: requireClient(clientId).version + 1 };
      clients.set(clientId, next);
      return respond(next);
    },
    attachChannel: (clientId, channelId) => {
      const client = requireClient(clientId);
      const chosen = Object.values(channels).find((item) => item.channel_id === channelId) ?? null;
      const next: Client = { ...client, own_channel: chosen ? { ...chosen, verified: true, verified_by: "manual" } : null, version: client.version + 1 };
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
      const confirmed = [...competitorState.values()].filter((item) => item.role === "competitor_set" && (item.status === "confirmed" || item.status === "user_added")).length;
      return respond({
        run,
        validation: {
          competitors_confirmed: confirmed,
          keywords_active: [...keywordState.values()].filter((item) => item.status === "confirmed").length,
          buyer_questions_active: [...questionState.values()].filter((item) => item.status === "confirmed").length,
        },
      });
    },
  },
  profile: {
    get: () => respond(profileState),
    update: (_clientId, patch, version) => {
      if (version !== profileState.version) return Promise.reject(new ApiError(412, "precondition_failed", "Someone else changed this. We loaded the latest version."));
      profileState = { ...applyProfileView(profileState, patch), version: profileState.version + 1 };
      return respond(profileState);
    },
    recrawl: (clientId) => respond(startMockRun(clientId, "recrawl", 30)),
  },
  keywords: topicApi(keywordState, "kw"),
  buyerQuestions: topicApi(questionState, "bq"),
  competitors: {
    list: (_clientId, status) => respond(page([...competitorState.values()].filter((item) => !status || item.status === status))),
    add: (_clientId, input) => {
      const handle = input.channel_ref.startsWith("@") ? input.channel_ref : `@${input.channel_ref}`;
      const channel = { ...channels.fortis, channel_id: `UCuser${Date.now()}`, title: handle, handle, subscriber_count: null };
      const added: CompetitorView = {
        ...competitorViews[0],
        id: `cmp_user_${Date.now()}`,
        status: "user_added",
        role: input.role,
        name: handle,
        channel,
        channels: (competitorViews[0].channels ?? []).map((item) => ({ ...item, channel, preview_video_ids: [] })),
        added_by: "user",
        sources: ["user"],
        evidence_summary: "Added by you",
      };
      competitorState.set(added.id, added);
      return respond(added);
    },
    suggest: (clientId) => respond(startMockRun(clientId, "onboarding", 30)),
    confirm: (_clientId, competitorId) => {
      const confirmed = [...competitorState.values()].filter((item) => item.role === "competitor_set" && (item.status === "confirmed" || item.status === "user_added")).length;
      if (confirmed >= 10) throw new ApiError(422, "competitors_out_of_range", "You can track up to 10. Move extras to 'Also watch'.");
      return respond(updateCompetitor(competitorId, { status: "confirmed", reject_reason: null }));
    },
    reject: (_clientId, competitorId, input) =>
      respond(updateCompetitor(competitorId, { status: "rejected", reject_reason: input.reason ?? "other" })),
    remove: (_clientId, competitorId) => {
      competitorState.delete(competitorId);
      return respond(undefined);
    },
    move: (_clientId, competitorId, role) => respond(updateCompetitor(competitorId, { role })),
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
    trending: (_clientId, windowDays) => respond(envelope(trending, windowDays)),
    outliers: (_clientId, query) => {
      const scoped = query.scope === "own" ? [] : outliers;
      return respond(envelope(page(scoped), query.window_days));
    },
    patterns: (_clientId, _scope, windowDays) => respond(envelope(competitorDetail(channels.fortis.channel_id)?.trait_lift ?? [], windowDays)),
    gaps: (_clientId, windowDays) => respond(envelope(gapMatrix(), windowDays)),
    answerCitations: () => respond(answerCitations()),
    topics: (_clientId, windowDays) =>
      respond(envelope(buyerQuestions.map((item, index) => ({ topic_ref: { id: item.id, label: item.text }, own_videos: index % 3, competitor_videos: 6 - index, outliers: index % 2, review_flagged: 0 })), windowDays)),
  },
  recommendations: {
    list: (_clientId, query) => {
      const windowDays = query.window_days ?? 30;
      const items = recommendations.map(withFeedback).filter((item) => (!query.type || item.type === query.type) && (!query.lens || item.lens.includes(query.lens)));
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
      return respond(withFeedback(found));
    },
    feedback: (recommendationId, input) => {
      if (input.status === null) feedbackState.delete(recommendationId);
      else feedbackState.set(recommendationId, input);
      return respond({ feedback: input });
    },
  },
  news: {
    opportunities: () =>
      respond({
        ...envelope(page([...newsState.values()]), 30),
        notices: [{ code: "news_partial", level: "warning" as const, message: "One news source didn't respond this morning, so this list may be missing a story or two." }],
      }),
    signals: () => respond(page([])),
    earlySignals: (_clientId, windowDays) => respond(envelope(earlySignals, windowDays)),
    dismiss: (opportunityId) => {
      const found = newsState.get(opportunityId);
      if (!found) throw new ApiError(404, "not_found", "We couldn't find that opportunity.");
      const next = { ...found, status: "dismissed" as const };
      newsState.set(opportunityId, next);
      return respond(next);
    },
  },
  refresh: {
    list: () => respond(envelope(page([...refreshState.values()].filter((item) => item.basis !== "rule_based")), 30)),
    hygiene: () => {
      const items = [...refreshState.values()].filter((item) => item.basis === "rule_based");
      return respond(envelope({ items, limit: 25, next_cursor: null, headline: "Two small fixes, each from a fixed rule.", eligibility: null }, 30));
    },
    applied: (suggestionId) => {
      const found = refreshState.get(suggestionId);
      if (!found) throw new ApiError(404, "not_found", "We couldn't find that suggestion.");
      const next = { ...found, status: "applied" as const, applied_at: new Date().toISOString() };
      refreshState.set(suggestionId, next);
      return respond(next);
    },
    dismiss: (suggestionId) => {
      const found = refreshState.get(suggestionId);
      if (!found) throw new ApiError(404, "not_found", "We couldn't find that suggestion.");
      const next = { ...found, status: "dismissed" as const };
      refreshState.set(suggestionId, next);
      return respond(next);
    },
  },
  creators: {
    list: (_clientId, windowDays, query) => {
      const sort = query?.sort ?? "matching_uploads";
      const value = (item: (typeof creators)[number]) => (sort === "uploads_90d" ? item.uploads_90d : item.match_share);
      const items = [...creators].sort((a, b) => value(b) - value(a)).map((item, index) => ({ ...item, sort, sort_value: value(item), position: index + 1 }));
      return respond(envelope({ items, sort, available_sorts: ["matching_uploads", "uploads_90d"], limit: 25, next_cursor: null }, windowDays));
    },
  },
  settings: {
    llm: () => respond(llmSettings),
    updateLlm: (input) => {
      const providers = { ...llmSettings.providers };
      for (const [id, patch] of Object.entries(input.providers ?? {})) {
        const current = providers[id];
        if (!current || !patch) continue;
        const base = patch.reset ? provider(current.kind, current.kind === "anthropic" ? "https://api.anthropic.com" : null, current.api_key_set) : current;
        const keySet = patch.clear_api_key ? false : patch.api_key ? true : base.api_key_set;
        const baseUrl = patch.base_url === undefined ? base.base_url : patch.base_url;
        const tiers = { ...base.tiers };
        for (const tier of ["low", "medium", "high"] as const) {
          const change = patch.tiers?.[tier];
          if (change) tiers[tier] = { ...tiers[tier], ...Object.fromEntries(Object.entries(change).filter(([, value]) => value !== null && value !== undefined)) };
        }
        providers[id] = {
          ...base,
          enabled: patch.enabled ?? base.enabled,
          base_url: baseUrl,
          tiers,
          api_key_set: keySet,
          api_key_source: keySet ? (patch.api_key ? "workspace" : base.api_key_source) : null,
          configured: keySet && (base.kind === "anthropic" || Boolean(baseUrl)),
        };
      }
      llmSettings = { enabled: input.enabled ?? llmSettings.enabled, provider_order: input.provider_order ?? llmSettings.provider_order, providers, updated_at: new Date().toISOString() };
      return respond(llmSettings);
    },
    testLlm: (id) =>
      respond({
        ok: true,
        checked_at: new Date().toISOString(),
        checks: (id ? [id] : (["anthropic", "openai_compat"] as const)).map((name) => {
          const current = llmSettings.providers[name];
          return current?.configured
            ? { provider: name, model: current.tiers.medium.model, status: "ok" as const, detail: "ready" }
            : { provider: name, model: null, status: "not_configured" as const, detail: "add an API key" };
        }),
      }),
    notifications: (clientId) =>
      respond(notificationState.get(clientId) ?? { email: "priya@demo-agency.in", first_brief_ready: true, daily_digest: false, news_closing_soon: true, delivery_enabled: false }),
    updateNotifications: (clientId, patch) => {
      const next: NotificationSettings = { email: null, first_brief_ready: true, daily_digest: false, news_closing_soon: true, delivery_enabled: false, ...notificationState.get(clientId), ...Object.fromEntries(Object.entries(patch).filter(([key, value]) => value !== null || key === "email")), updated_at: new Date().toISOString() };
      notificationState.set(clientId, next);
      return respond(next);
    },
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
