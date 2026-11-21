import {
  toClient,
  toCompetitorDetail,
  toEnvelope,
  toEvent,
  toGapMatrix,
  toOutlierPage,
  toOwnPerformance,
  rawProfileItems,
  toProfilePatch,
  toProfileView,
  toRecommendation,
  toRecommendationPage,
  toRun,
  toScorecard,
  toTrending,
} from "./adapters";
import type { GrowthHackerApi, TopicApi } from "./contract";
import { apiPath, request, send } from "./http";
import type { components } from "./schema";
import type { BuyerQuestionCreate, BuyerQuestionItem, KeywordCreate, KeywordItem, Page, ResultEnvelope } from "./types";

type Wire = components["schemas"];

// review screens show every item, so lists follow the cursor instead of stopping at the first page
async function allPages<T>(path: string, query: Record<string, string | undefined>): Promise<Page<T>> {
  const items: T[] = [];
  let cursor: string | undefined;
  for (;;) {
    const page = await request<Page<T>>("GET", path, { query: { ...query, limit: 100, cursor } });
    items.push(...page.items);
    if (!page.next_cursor || !page.items.length) return { ...page, items, next_cursor: null };
    cursor = page.next_cursor;
  }
}

function topicApi<T, C>(resource: "keywords" | "buyer-questions"): TopicApi<T, C> {
  const base = (clientId: string) => apiPath`/clients/${clientId}/${resource}`;
  return {
    list: (clientId, status) => allPages(base(clientId), { status }),
    create: (clientId, input) => request("POST", base(clientId), { body: input }),
    update: (clientId, id, patch) => request("PATCH", base(clientId) + apiPath`/${id}`, { body: patch }),
    remove: (clientId, id) => request("DELETE", base(clientId) + apiPath`/${id}`),
    bulkStatus: (clientId, input) => request("POST", `${base(clientId)}/bulk-status`, { body: input }),
  };
}

// every domain item seen per client, so an undo can send a removed item back with all its fields
const rawItems = new Map<string, Record<string, unknown>>();

function rememberProfile(clientId: string, view: Wire["BusinessProfileView"]) {
  for (const profile of [view.profile, view.effective]) for (const [key, item] of rawProfileItems(profile)) rawItems.set(`${clientId}:${key}`, item);
  return toProfileView(view);
}

const run = async (pending: Promise<Wire["RunOut"]>) => toRun(await pending);

export const httpApi: GrowthHackerApi = {
  auth: {
    login: (input) => request("POST", "/auth/login", { body: input }),
    logout: () => request("POST", "/auth/logout"),
    me: () => request("GET", "/me"),
    switchWorkspace: (workspaceId) => request("POST", "/auth/workspace", { body: { workspace_id: workspaceId } }),
  },
  clients: {
    list: async (query) => {
      const page = await request<Wire["Page_ClientOut_"]>("GET", "/clients", { query: { ...query } });
      return { items: page.items.map(toClient), next_cursor: page.next_cursor ?? null, limit: page.limit, total: page.total ?? null };
    },
    create: async (input, idempotencyKey) => {
      const created = await request<Wire["ClientCreatedOut"]>("POST", "/clients", { body: input, idempotencyKey });
      return { client: toClient(created.client), onboarding_run: created.onboarding_run ? toRun(created.onboarding_run) : null };
    },
    get: async (clientId) => toClient(await request("GET", apiPath`/clients/${clientId}`)),
    update: async (clientId, patch, version) => toClient((await request<Wire["ClientUpdateOut"]>("PATCH", apiPath`/clients/${clientId}`, { body: patch, ifMatch: version })).client),
    attachChannel: async (clientId, channelId) => toClient(await request("POST", apiPath`/clients/${clientId}/channel/attach`, { body: { channel_id: channelId } })),
    remove: (clientId) => request("DELETE", apiPath`/clients/${clientId}`),
    resolveChannel: async (clientId, channelRef) => {
      const out = await request<Wire["ChannelResolveOut"]>("POST", apiPath`/clients/${clientId}/channel/resolve`, { body: { channel_ref: channelRef } });
      return { candidates: out.candidates.map((candidate) => ({ ...candidate, channel: { ...candidate.channel, title: candidate.channel.title ?? candidate.channel.channel_id, handle: candidate.channel.handle ?? null, thumbnail_url: candidate.channel.thumbnail_url ?? null, subscriber_count: candidate.channel.subscriber_count ?? null, verified_by: candidate.channel.verified_by ?? null } })) };
    },
    restartOnboarding: (clientId, input) => run(request("POST", apiPath`/clients/${clientId}/onboarding`, { body: input })),
    confirmSetup: async (clientId, windowDays, idempotencyKey) => {
      const out = await request<Wire["ConfirmSetupOut"]>("POST", apiPath`/clients/${clientId}/confirm-setup`, { body: { window_days: windowDays }, idempotencyKey });
      return { run: toRun(out.run), validation: out.validation };
    },
  },
  profile: {
    get: async (clientId) => {
      const view = await request<Wire["BusinessProfileView"]>("GET", apiPath`/clients/${clientId}/profile`);
      return rememberProfile(clientId, view);
    },
    update: async (clientId, patch, version) => {
      const body = toProfilePatch(patch, (key) => rawItems.get(`${clientId}:${key}`));
      const view = await request<Wire["BusinessProfileView"]>("PATCH", apiPath`/clients/${clientId}/profile`, { body, ifMatch: version, contentType: "application/merge-patch+json" });
      return rememberProfile(clientId, view);
    },
    recrawl: (clientId) => run(request("POST", apiPath`/clients/${clientId}/profile/recrawl`)),
  },
  keywords: topicApi<KeywordItem, KeywordCreate>("keywords"),
  buyerQuestions: topicApi<BuyerQuestionItem, BuyerQuestionCreate>("buyer-questions"),
  competitors: {
    list: (clientId, status) => allPages(apiPath`/clients/${clientId}/competitors`, { status }),
    add: (clientId, input, idempotencyKey) => request("POST", apiPath`/clients/${clientId}/competitors`, { body: input, idempotencyKey }),
    suggest: (clientId) => run(request("POST", apiPath`/clients/${clientId}/competitors/suggest`)),
    confirm: (clientId, competitorId) => request("POST", apiPath`/clients/${clientId}/competitors/${competitorId}/confirm`),
    reject: (clientId, competitorId, input) => request("POST", apiPath`/clients/${clientId}/competitors/${competitorId}/reject`, { body: input }),
    remove: (clientId, competitorId) => request("DELETE", apiPath`/clients/${clientId}/competitors/${competitorId}`),
    move: (clientId, competitorId, role) => request("PATCH", apiPath`/clients/${clientId}/competitors/${competitorId}`, { body: { role } }),
  },
  runs: {
    create: (clientId, input, idempotencyKey) => run(request("POST", apiPath`/clients/${clientId}/runs`, { body: input, idempotencyKey })),
    list: async (clientId, query) => {
      const page = await request<Wire["Page_RunOut_"]>("GET", apiPath`/clients/${clientId}/runs`, { query: { ...query } });
      return { items: page.items.map(toRun), next_cursor: page.next_cursor ?? null, limit: page.limit, total: page.total ?? null };
    },
    get: (runId) => run(request("GET", apiPath`/runs/${runId}`)),
    cancel: (runId, reason) => run(request("POST", apiPath`/runs/${runId}/cancel`, { body: { reason } })),
    retry: (runId, fromStage) => run(request("POST", apiPath`/runs/${runId}/retry`, { body: { from_stage: fromStage } })),
    events: async (runId, afterSeq, limit = 200) => {
      const page = await request<Wire["EventsPage"]>("GET", apiPath`/runs/${runId}/events.json`, { query: { after_seq: afterSeq, limit } });
      return { items: page.items.map(toEvent), last_seq: page.last_seq, run_status: page.run_status };
    },
    streamToken: (runId) => request("POST", apiPath`/runs/${runId}/stream-token`),
  },
  analytics: {
    ownPerformance: async (clientId, windowDays) =>
      toEnvelope(await request<ResultEnvelope<Wire["OwnPerformanceData"]>>("GET", apiPath`/clients/${clientId}/analytics/own-performance`, { query: { window_days: windowDays } }), toOwnPerformance),
    competitors: async (clientId, windowDays) =>
      toEnvelope(await request<ResultEnvelope<Wire["CompetitorSheet"][]>>("GET", apiPath`/clients/${clientId}/analytics/competitors`, { query: { window_days: windowDays } }), (items) => items.map((sheet) => toScorecard(sheet.channel, sheet.scorecard, sheet.top_outliers))),
    competitor: async (clientId, channelId, windowDays) =>
      toEnvelope(await request<ResultEnvelope<Wire["CompetitorDetail"]>>("GET", apiPath`/clients/${clientId}/analytics/competitors/${channelId}`, { query: { window_days: windowDays } }), toCompetitorDetail),
    trending: async (clientId, windowDays) => toEnvelope(await request<ResultEnvelope<Wire["TrendingData"]>>("GET", apiPath`/clients/${clientId}/analytics/trending`, { query: { window_days: windowDays } }), toTrending),
    outliers: async (clientId, query) => toEnvelope(await request<ResultEnvelope<Wire["Page_OutlierRow_"]>>("GET", apiPath`/clients/${clientId}/analytics/outliers`, { query: { ...query } }), toOutlierPage),
    patterns: async (clientId, scope, windowDays) => toEnvelope(await request<ResultEnvelope<Wire["TraitLift"][]>>("GET", apiPath`/clients/${clientId}/analytics/patterns`, { query: { scope, window_days: windowDays } }), (items) => toCompetitorDetail({ trait_lift: items } as Wire["CompetitorDetail"]).trait_lift),
    topics: async (clientId, windowDays) => toEnvelope(await request<ResultEnvelope<Wire["TopicSummary"][]>>("GET", apiPath`/clients/${clientId}/analytics/topics`, { query: { window_days: windowDays } }), (items) => items),
    gaps: async (clientId, windowDays) => toEnvelope(await request<ResultEnvelope<Wire["GapMatrixData"]>>("GET", apiPath`/clients/${clientId}/analytics/gaps`, { query: { window_days: windowDays } }), toGapMatrix),
    landscape: async (clientId) => toEnvelope(await request<ResultEnvelope<Wire["ChannelLandscape"]>>("GET", apiPath`/clients/${clientId}/analytics/landscape`), (data) => data),
    answerCitations: (clientId) => request<Wire["AnswerCitationsData"]>("GET", apiPath`/clients/${clientId}/answer-citations`),
  },
  trends: {
    latest: async (clientId) => toEnvelope(await request<ResultEnvelope<Wire["TrendScan"]>>("GET", apiPath`/clients/${clientId}/trends`), (data) => data),
  },
  recommendations: {
    list: async (clientId, query) => {
      const response = await send<Wire["ResultEnvelope_RecommendationsData_"] | { cards_run: Wire["RunOut"] }>("GET", apiPath`/clients/${clientId}/recommendations`, { query: { ...query } });
      if (response.status === 202 && "cards_run" in response.data) return { state: "writing", cards_run: toRun(response.data.cards_run) };
      return { state: "ready", envelope: toRecommendationPage(response.data as Wire["ResultEnvelope_RecommendationsData_"]) };
    },
    get: async (recommendationId) => toRecommendation(await request("GET", apiPath`/recommendations/${recommendationId}`)),
    feedback: (recommendationId, input, idempotencyKey) => request("PUT", apiPath`/recommendations/${recommendationId}/feedback`, { body: input, idempotencyKey }),
  },
  news: {
    opportunities: (clientId, query) => request("GET", apiPath`/clients/${clientId}/news/opportunities`, { query: { ...query } }),
    signals: (clientId, query) => request("GET", apiPath`/clients/${clientId}/news/signals`, { query: { ...query } }),
    earlySignals: (clientId, windowDays) => request("GET", apiPath`/clients/${clientId}/early-signals`, { query: { window_days: windowDays } }),
    dismiss: (opportunityId) => request("POST", apiPath`/news/opportunities/${opportunityId}/dismiss`),
  },
  refresh: {
    list: (clientId, query) => request("GET", apiPath`/clients/${clientId}/refresh-suggestions`, { query: { ...query } }),
    applied: (suggestionId, input) => request("POST", apiPath`/refresh-suggestions/${suggestionId}/applied`, { body: input }),
    dismiss: (suggestionId) => request("POST", apiPath`/refresh-suggestions/${suggestionId}/dismiss`),
    hygiene: (clientId) => request("GET", apiPath`/clients/${clientId}/hygiene`),
  },
  creators: {
    list: (clientId, windowDays, query) => request("GET", apiPath`/clients/${clientId}/creators`, { query: { window_days: windowDays, ...query } }),
  },
  settings: {
    llm: () => request("GET", "/settings/llm"),
    updateLlm: (input) => request("PUT", "/settings/llm", { body: input }),
    testLlm: (provider) => request("POST", "/settings/llm/test", { body: { provider } }),
    notifications: (clientId) => request("GET", apiPath`/clients/${clientId}/notification-settings`),
    updateNotifications: (clientId, patch) => request("PATCH", apiPath`/clients/${clientId}/notification-settings`, { body: patch }),
  },
  integrations: {
    youtubeConnect: (clientId) => request("GET", "/integrations/youtube/connect", { query: { client_id: clientId } }),
    youtubeDisconnect: (clientId) => request("DELETE", apiPath`/clients/${clientId}/integrations/youtube`),
  },
  evaluation: {
    startBacktest: (input) => run(request("POST", "/evaluation/backtests", { body: input })),
    outlierLabels: (clientId, query) => request("GET", "/evaluation/outlier-labels", { query: { client_id: clientId, ...query } }),
    putOutlierLabel: (videoId, label) => request("PUT", apiPath`/evaluation/outlier-labels/${videoId}`, { body: label }),
    outlierPrecision: (clientId) => request("GET", "/evaluation/outlier-precision", { query: { client_id: clientId } }),
    submitReview: (rating) => request("POST", "/evaluation/reviews", { body: rating }),
  },
  ops: {
    quota: (date) => request("GET", "/ops/quota", { query: { date } }),
  },
};
