import type { GrowthHackerApi, RecommendationPage, TopicApi } from "./contract";
import { request, send } from "./http";
import type { BuyerQuestionItem, KeywordItem, PipelineRun } from "./types";

function topicApi<T>(resource: "keywords" | "buyer-questions"): TopicApi<T> {
  const base = (clientId: string) => `/clients/${clientId}/${resource}`;
  return {
    list: (clientId, status) => request("GET", base(clientId), { query: { status } }),
    create: (clientId, input) => request("POST", base(clientId), { body: input }),
    update: (clientId, id, patch) => request("PATCH", `${base(clientId)}/${id}`, { body: patch }),
    remove: (clientId, id) => request("DELETE", `${base(clientId)}/${id}`),
    bulkStatus: (clientId, input) => request("POST", `${base(clientId)}/bulk-status`, { body: input }),
  };
}

export const httpApi: GrowthHackerApi = {
  clients: {
    list: (query) => request("GET", "/clients", { query: { ...query } }),
    create: (input, idempotencyKey) => request("POST", "/clients", { body: input, idempotencyKey }),
    get: (clientId) => request("GET", `/clients/${clientId}`),
    update: (clientId, patch, version) =>
      request("PATCH", `/clients/${clientId}`, { body: patch, ifMatch: version, contentType: "application/merge-patch+json" }),
    remove: (clientId) => request("DELETE", `/clients/${clientId}`),
    resolveChannel: (clientId, channelRef) =>
      request("POST", `/clients/${clientId}/channel/resolve`, { body: { channel_ref: channelRef } }),
    restartOnboarding: (clientId, input) => request("POST", `/clients/${clientId}/onboarding`, { body: input }),
    confirmSetup: (clientId, windowDays, idempotencyKey) =>
      request("POST", `/clients/${clientId}/confirm-setup`, { body: { window_days: windowDays }, idempotencyKey }),
  },
  profile: {
    get: (clientId) => request("GET", `/clients/${clientId}/profile`),
    update: (clientId, patch, version) =>
      request("PATCH", `/clients/${clientId}/profile`, { body: patch, ifMatch: version, contentType: "application/merge-patch+json" }),
    recrawl: (clientId) => request("POST", `/clients/${clientId}/profile/recrawl`),
  },
  keywords: topicApi<KeywordItem>("keywords"),
  buyerQuestions: topicApi<BuyerQuestionItem>("buyer-questions"),
  competitors: {
    list: (clientId, status) => request("GET", `/clients/${clientId}/competitors`, { query: { status } }),
    add: (clientId, input, idempotencyKey) =>
      request("POST", `/clients/${clientId}/competitors`, { body: input, idempotencyKey }),
    suggest: (clientId) => request("POST", `/clients/${clientId}/competitors/suggest`),
    confirm: (clientId, competitorId) => request("POST", `/clients/${clientId}/competitors/${competitorId}/confirm`),
    reject: (clientId, competitorId, input) =>
      request("POST", `/clients/${clientId}/competitors/${competitorId}/reject`, { body: input }),
    remove: (clientId, competitorId) => request("DELETE", `/clients/${clientId}/competitors/${competitorId}`),
  },
  runs: {
    create: (clientId, input, idempotencyKey) =>
      request("POST", `/clients/${clientId}/runs`, { body: input, idempotencyKey }),
    list: (clientId, query) => request("GET", `/clients/${clientId}/runs`, { query: { ...query } }),
    get: (runId) => request("GET", `/runs/${runId}`),
    cancel: (runId, reason) => request("POST", `/runs/${runId}/cancel`, { body: { reason } }),
    retry: (runId, fromStage) => request("POST", `/runs/${runId}/retry`, { body: { from_stage: fromStage } }),
    events: (runId, afterSeq, limit = 200) =>
      request("GET", `/runs/${runId}/events.json`, { query: { after_seq: afterSeq, limit } }),
    streamToken: (runId) => request("POST", `/runs/${runId}/stream-token`),
  },
  analytics: {
    ownPerformance: (clientId, windowDays) =>
      request("GET", `/clients/${clientId}/analytics/own-performance`, { query: { window_days: windowDays } }),
    competitors: (clientId, windowDays, sort) =>
      request("GET", `/clients/${clientId}/analytics/competitors`, { query: { window_days: windowDays, sort } }),
    competitor: (clientId, channelId, windowDays) =>
      request("GET", `/clients/${clientId}/analytics/competitors/${channelId}`, { query: { window_days: windowDays } }),
    competitorVideos: (clientId, channelId, query) =>
      request("GET", `/clients/${clientId}/analytics/competitors/${channelId}/videos`, { query: { ...query } }),
    trending: (clientId, windowDays) =>
      request("GET", `/clients/${clientId}/analytics/trending`, { query: { window_days: windowDays } }),
    outliers: (clientId, query) => request("GET", `/clients/${clientId}/analytics/outliers`, { query: { ...query } }),
    patterns: (clientId, scope, windowDays) =>
      request("GET", `/clients/${clientId}/analytics/patterns`, { query: { scope, window_days: windowDays } }),
    topics: (clientId, windowDays) =>
      request("GET", `/clients/${clientId}/analytics/topics`, { query: { window_days: windowDays } }),
  },
  recommendations: {
    list: async (clientId, query) => {
      const response = await send<RecommendationPage | { cards_run: PipelineRun }>(
        "GET",
        `/clients/${clientId}/recommendations`,
        { query: { ...query } },
      );
      if (response.status === 202 && "cards_run" in response.data) {
        return { state: "writing", cards_run: response.data.cards_run };
      }
      return { state: "ready", envelope: response.data as RecommendationPage };
    },
    get: (recommendationId) => request("GET", `/recommendations/${recommendationId}`),
    feedback: (recommendationId, input, idempotencyKey) =>
      request("PUT", `/recommendations/${recommendationId}/feedback`, { body: input, idempotencyKey }),
  },
  news: {
    opportunities: (clientId, query) =>
      request("GET", `/clients/${clientId}/news/opportunities`, { query: { ...query } }),
    signals: (clientId, query) => request("GET", `/clients/${clientId}/news/signals`, { query: { ...query } }),
    dismiss: (opportunityId) => request("POST", `/news/opportunities/${opportunityId}/dismiss`),
  },
  refresh: {
    list: (clientId, query) => request("GET", `/clients/${clientId}/refresh-suggestions`, { query: { ...query } }),
    applied: (suggestionId, input) => request("POST", `/refresh-suggestions/${suggestionId}/applied`, { body: input }),
    dismiss: (suggestionId) => request("POST", `/refresh-suggestions/${suggestionId}/dismiss`),
  },
  creators: {
    list: (clientId, windowDays, query) =>
      request("GET", `/clients/${clientId}/creators`, { query: { window_days: windowDays, ...query } }),
  },
  integrations: {
    youtubeConnect: (clientId) => request("GET", "/integrations/youtube/connect", { query: { client_id: clientId } }),
    youtubeDisconnect: (clientId) => request("DELETE", `/clients/${clientId}/integrations/youtube`),
  },
  evaluation: {
    startBacktest: (input) => request("POST", "/evaluation/backtests", { body: input }),
    outlierLabels: (clientId, query) =>
      request("GET", "/evaluation/outlier-labels", { query: { client_id: clientId, ...query } }),
    putOutlierLabel: (videoId, label) => request("PUT", `/evaluation/outlier-labels/${videoId}`, { body: label }),
    outlierPrecision: (clientId) => request("GET", "/evaluation/outlier-precision", { query: { client_id: clientId } }),
    submitReview: (rating) => request("POST", "/evaluation/reviews", { body: rating }),
  },
  ops: {
    quota: (date) => request("GET", "/ops/quota", { query: { date } }),
  },
};
