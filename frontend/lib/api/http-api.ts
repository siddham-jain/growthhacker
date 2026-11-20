import {
  toClient,
  toCompetitorDetail,
  toEnvelope,
  toEvent,
  toGapMatrix,
  toOutlierPage,
  toOwnPerformance,
  toProfilePatch,
  toProfileView,
  toRecommendation,
  toRecommendationPage,
  toRun,
  toScorecard,
  toTrending,
} from "./adapters";
import type { GrowthHackerApi, TopicApi } from "./contract";
import { request, send } from "./http";
import type { components } from "./schema";
import type { BuyerQuestionCreate, BuyerQuestionItem, KeywordCreate, KeywordItem, ResultEnvelope } from "./types";

type Wire = components["schemas"];

function topicApi<T, C>(resource: "keywords" | "buyer-questions"): TopicApi<T, C> {
  const base = (clientId: string) => `/clients/${clientId}/${resource}`;
  return {
    list: (clientId, status) => request("GET", base(clientId), { query: { status } }),
    create: (clientId, input) => request("POST", base(clientId), { body: input }),
    update: (clientId, id, patch) => request("PATCH", `${base(clientId)}/${id}`, { body: patch }),
    remove: (clientId, id) => request("DELETE", `${base(clientId)}/${id}`),
    bulkStatus: (clientId, input) => request("POST", `${base(clientId)}/bulk-status`, { body: input }),
  };
}

// the profile patch is written against the domain profile last read, so keep that copy per client
const rawProfiles = new Map<string, Wire["BusinessProfile"]>();

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
    get: async (clientId) => toClient(await request("GET", `/clients/${clientId}`)),
    update: async (clientId, patch, version) => toClient((await request<Wire["ClientUpdateOut"]>("PATCH", `/clients/${clientId}`, { body: patch, ifMatch: version })).client),
    attachChannel: async (clientId, channelId) => toClient(await request("POST", `/clients/${clientId}/channel/attach`, { body: { channel_id: channelId } })),
    remove: (clientId) => request("DELETE", `/clients/${clientId}`),
    resolveChannel: async (clientId, channelRef) => {
      const out = await request<Wire["ChannelResolveOut"]>("POST", `/clients/${clientId}/channel/resolve`, { body: { channel_ref: channelRef } });
      return { candidates: out.candidates.map((candidate) => ({ ...candidate, channel: { ...candidate.channel, title: candidate.channel.title ?? candidate.channel.channel_id, handle: candidate.channel.handle ?? null, thumbnail_url: candidate.channel.thumbnail_url ?? null, subscriber_count: candidate.channel.subscriber_count ?? null, verified_by: candidate.channel.verified_by ?? null } })) };
    },
    restartOnboarding: (clientId, input) => run(request("POST", `/clients/${clientId}/onboarding`, { body: input })),
    confirmSetup: async (clientId, windowDays, idempotencyKey) => {
      const out = await request<Wire["ConfirmSetupOut"]>("POST", `/clients/${clientId}/confirm-setup`, { body: { window_days: windowDays }, idempotencyKey });
      return { run: toRun(out.run), validation: out.validation };
    },
  },
  profile: {
    get: async (clientId) => {
      const view = await request<Wire["BusinessProfileView"]>("GET", `/clients/${clientId}/profile`);
      rawProfiles.set(clientId, view.effective);
      return toProfileView(view);
    },
    update: async (clientId, patch, version) => {
      const raw = rawProfiles.get(clientId);
      const body = raw ? toProfilePatch(raw, patch as Parameters<typeof toProfilePatch>[1]) : patch;
      const view = await request<Wire["BusinessProfileView"]>("PATCH", `/clients/${clientId}/profile`, { body, ifMatch: version, contentType: "application/merge-patch+json" });
      rawProfiles.set(clientId, view.effective);
      return toProfileView(view);
    },
    recrawl: (clientId) => run(request("POST", `/clients/${clientId}/profile/recrawl`)),
  },
  keywords: topicApi<KeywordItem, KeywordCreate>("keywords"),
  buyerQuestions: topicApi<BuyerQuestionItem, BuyerQuestionCreate>("buyer-questions"),
  competitors: {
    list: (clientId, status) => request("GET", `/clients/${clientId}/competitors`, { query: { status } }),
    add: (clientId, input, idempotencyKey) => request("POST", `/clients/${clientId}/competitors`, { body: input, idempotencyKey }),
    suggest: (clientId) => run(request("POST", `/clients/${clientId}/competitors/suggest`)),
    confirm: (clientId, competitorId) => request("POST", `/clients/${clientId}/competitors/${competitorId}/confirm`),
    reject: (clientId, competitorId, input) => request("POST", `/clients/${clientId}/competitors/${competitorId}/reject`, { body: input }),
    remove: (clientId, competitorId) => request("DELETE", `/clients/${clientId}/competitors/${competitorId}`),
    move: (clientId, competitorId, role) => request("PATCH", `/clients/${clientId}/competitors/${competitorId}`, { body: { role } }),
  },
  runs: {
    create: (clientId, input, idempotencyKey) => run(request("POST", `/clients/${clientId}/runs`, { body: input, idempotencyKey })),
    list: async (clientId, query) => {
      const page = await request<Wire["Page_RunOut_"]>("GET", `/clients/${clientId}/runs`, { query: { ...query } });
      return { items: page.items.map(toRun), next_cursor: page.next_cursor ?? null, limit: page.limit, total: page.total ?? null };
    },
    get: (runId) => run(request("GET", `/runs/${runId}`)),
    cancel: (runId, reason) => run(request("POST", `/runs/${runId}/cancel`, { body: { reason } })),
    retry: (runId, fromStage) => run(request("POST", `/runs/${runId}/retry`, { body: { from_stage: fromStage } })),
    events: async (runId, afterSeq, limit = 200) => {
      const page = await request<Wire["EventsPage"]>("GET", `/runs/${runId}/events.json`, { query: { after_seq: afterSeq, limit } });
      return { items: page.items.map(toEvent), last_seq: page.last_seq, run_status: page.run_status };
    },
    streamToken: (runId) => request("POST", `/runs/${runId}/stream-token`),
  },
  analytics: {
    ownPerformance: async (clientId, windowDays) =>
      toEnvelope(await request<ResultEnvelope<Wire["OwnPerformanceData"]>>("GET", `/clients/${clientId}/analytics/own-performance`, { query: { window_days: windowDays } }), toOwnPerformance),
    competitors: async (clientId, windowDays) =>
      toEnvelope(await request<ResultEnvelope<Wire["CompetitorSheet"][]>>("GET", `/clients/${clientId}/analytics/competitors`, { query: { window_days: windowDays } }), (items) => items.map((sheet) => toScorecard(sheet.channel, sheet.scorecard, sheet.top_outliers))),
    competitor: async (clientId, channelId, windowDays) =>
      toEnvelope(await request<ResultEnvelope<Wire["CompetitorDetail"]>>("GET", `/clients/${clientId}/analytics/competitors/${channelId}`, { query: { window_days: windowDays } }), toCompetitorDetail),
    trending: async (clientId, windowDays) => toEnvelope(await request<ResultEnvelope<Wire["TrendingData"]>>("GET", `/clients/${clientId}/analytics/trending`, { query: { window_days: windowDays } }), toTrending),
    outliers: async (clientId, query) => toEnvelope(await request<ResultEnvelope<Wire["Page_OutlierRow_"]>>("GET", `/clients/${clientId}/analytics/outliers`, { query: { ...query } }), toOutlierPage),
    patterns: async (clientId, scope, windowDays) => toEnvelope(await request<ResultEnvelope<Wire["TraitLift"][]>>("GET", `/clients/${clientId}/analytics/patterns`, { query: { scope, window_days: windowDays } }), (items) => toCompetitorDetail({ trait_lift: items } as Wire["CompetitorDetail"]).trait_lift),
    topics: async (clientId, windowDays) => toEnvelope(await request<ResultEnvelope<Wire["TopicSummary"][]>>("GET", `/clients/${clientId}/analytics/topics`, { query: { window_days: windowDays } }), (items) => items),
    gaps: async (clientId, windowDays) => toEnvelope(await request<ResultEnvelope<Wire["GapMatrixData"]>>("GET", `/clients/${clientId}/analytics/gaps`, { query: { window_days: windowDays } }), toGapMatrix),
  },
  recommendations: {
    list: async (clientId, query) => {
      const response = await send<Wire["ResultEnvelope_RecommendationsData_"] | { cards_run: Wire["RunOut"] }>("GET", `/clients/${clientId}/recommendations`, { query: { ...query } });
      if (response.status === 202 && "cards_run" in response.data) return { state: "writing", cards_run: toRun(response.data.cards_run) };
      return { state: "ready", envelope: toRecommendationPage(response.data as Wire["ResultEnvelope_RecommendationsData_"]) };
    },
    get: async (recommendationId) => toRecommendation(await request("GET", `/recommendations/${recommendationId}`)),
    feedback: (recommendationId, input, idempotencyKey) => request("PUT", `/recommendations/${recommendationId}/feedback`, { body: input, idempotencyKey }),
  },
  news: {
    opportunities: (clientId, query) => request("GET", `/clients/${clientId}/news/opportunities`, { query: { ...query } }),
    signals: (clientId, query) => request("GET", `/clients/${clientId}/news/signals`, { query: { ...query } }),
    earlySignals: (clientId, windowDays) => request("GET", `/clients/${clientId}/early-signals`, { query: { window_days: windowDays } }),
    dismiss: (opportunityId) => request("POST", `/news/opportunities/${opportunityId}/dismiss`),
  },
  refresh: {
    list: (clientId, query) => request("GET", `/clients/${clientId}/refresh-suggestions`, { query: { ...query } }),
    applied: (suggestionId, input) => request("POST", `/refresh-suggestions/${suggestionId}/applied`, { body: input }),
    dismiss: (suggestionId) => request("POST", `/refresh-suggestions/${suggestionId}/dismiss`),
    hygiene: (clientId) => request("GET", `/clients/${clientId}/hygiene`),
  },
  creators: {
    list: (clientId, windowDays, query) => request("GET", `/clients/${clientId}/creators`, { query: { window_days: windowDays, ...query } }),
  },
  settings: {
    llm: () => request("GET", "/settings/llm"),
    updateLlm: (input) => request("PUT", "/settings/llm", { body: input }),
    testLlm: (provider) => request("POST", "/settings/llm/test", { body: { provider } }),
    notifications: (clientId) => request("GET", `/clients/${clientId}/notification-settings`),
    updateNotifications: (clientId, patch) => request("PATCH", `/clients/${clientId}/notification-settings`, { body: patch }),
  },
  integrations: {
    youtubeConnect: (clientId) => request("GET", "/integrations/youtube/connect", { query: { client_id: clientId } }),
    youtubeDisconnect: (clientId) => request("DELETE", `/clients/${clientId}/integrations/youtube`),
  },
  evaluation: {
    startBacktest: (input) => run(request("POST", "/evaluation/backtests", { body: input })),
    outlierLabels: (clientId, query) => request("GET", "/evaluation/outlier-labels", { query: { client_id: clientId, ...query } }),
    putOutlierLabel: (videoId, label) => request("PUT", `/evaluation/outlier-labels/${videoId}`, { body: label }),
    outlierPrecision: (clientId) => request("GET", "/evaluation/outlier-precision", { query: { client_id: clientId } }),
    submitReview: (rating) => request("POST", "/evaluation/reviews", { body: rating }),
  },
  ops: {
    quota: (date) => request("GET", "/ops/quota", { query: { date } }),
  },
};
