import { queryOptions } from "@tanstack/react-query";
import { api } from "./index";
import type { RecommendationQuery } from "./contract";
import type { WindowDays } from "./types";

const MINUTE = 60_000;

export const clientKey = (clientId: string) => ["client", clientId] as const;

export const clientsQuery = () =>
  queryOptions({ queryKey: ["clients"], queryFn: () => api.clients.list(), staleTime: MINUTE });

export const clientQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId)], queryFn: () => api.clients.get(clientId), staleTime: MINUTE });

export const profileQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "profile"], queryFn: () => api.profile.get(clientId), staleTime: MINUTE });

export const keywordsQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "keywords"], queryFn: () => api.keywords.list(clientId) });

export const buyerQuestionsQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "buyer-questions"], queryFn: () => api.buyerQuestions.list(clientId) });

export const competitorsQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "competitors"], queryFn: () => api.competitors.list(clientId) });

export const runQuery = (runId: string) =>
  queryOptions({ queryKey: ["run", runId], queryFn: () => api.runs.get(runId), staleTime: 0 });

export const runsQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "runs"], queryFn: () => api.runs.list(clientId), staleTime: 10_000 });

export const recommendationsQuery = (clientId: string, query: RecommendationQuery) =>
  queryOptions({
    queryKey: [...clientKey(clientId), "recommendations", query],
    queryFn: () => api.recommendations.list(clientId, query),
    staleTime: MINUTE,
  });

export const ownPerformanceQuery = (clientId: string, windowDays: WindowDays) =>
  queryOptions({
    queryKey: [...clientKey(clientId), "own-performance", windowDays],
    queryFn: () => api.analytics.ownPerformance(clientId, windowDays),
    staleTime: MINUTE,
  });

export const scorecardsQuery = (clientId: string, windowDays: WindowDays) =>
  queryOptions({
    queryKey: [...clientKey(clientId), "scorecards", windowDays],
    queryFn: () => api.analytics.competitors(clientId, windowDays),
    staleTime: MINUTE,
  });

export const competitorDetailQuery = (clientId: string, channelId: string, windowDays: WindowDays) =>
  queryOptions({
    queryKey: [...clientKey(clientId), "competitor", channelId, windowDays],
    queryFn: () => api.analytics.competitor(clientId, channelId, windowDays),
    staleTime: MINUTE,
  });

export const trendingQuery = (clientId: string, windowDays: WindowDays) =>
  queryOptions({
    queryKey: [...clientKey(clientId), "trending", windowDays],
    queryFn: () => api.analytics.trending(clientId, windowDays),
    staleTime: MINUTE,
  });

export const outliersQuery = (clientId: string, windowDays: WindowDays) =>
  queryOptions({
    queryKey: [...clientKey(clientId), "outliers", windowDays],
    queryFn: () => api.analytics.outliers(clientId, { scope: "competitors", window_days: windowDays }),
    staleTime: MINUTE,
  });

export const newsQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "news"], queryFn: () => api.news.opportunities(clientId), staleTime: MINUTE });

export const earlySignalsQuery = (clientId: string, windowDays: WindowDays) =>
  queryOptions({ queryKey: [...clientKey(clientId), "early-signals", windowDays], queryFn: () => api.news.earlySignals(clientId, windowDays), staleTime: MINUTE });

export const hygieneQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "hygiene"], queryFn: () => api.refresh.hygiene(clientId), staleTime: MINUTE });

export const refreshQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "refresh"], queryFn: () => api.refresh.list(clientId), staleTime: MINUTE });

export const creatorsQuery = (clientId: string, windowDays: WindowDays, sort?: string) =>
  queryOptions({
    queryKey: [...clientKey(clientId), "creators", windowDays, sort ?? "default"],
    queryFn: () => api.creators.list(clientId, windowDays, { sort }),
    staleTime: MINUTE,
  });

export const meQuery = () => queryOptions({ queryKey: ["me"], queryFn: () => api.auth.me(), staleTime: 5 * MINUTE });

export const trendsQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "trends"], queryFn: () => api.trends.latest(clientId), staleTime: 10 * MINUTE });

export const landscapeQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "landscape"], queryFn: () => api.analytics.landscape(clientId), staleTime: 10 * MINUTE });

export const answerCitationsQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "answer-citations"], queryFn: () => api.analytics.answerCitations(clientId), staleTime: 10 * MINUTE });

export const gapsQuery = (clientId: string, windowDays: WindowDays) =>
  queryOptions({
    queryKey: [...clientKey(clientId), "gaps", windowDays],
    queryFn: () => api.analytics.gaps(clientId, windowDays),
    staleTime: MINUTE,
  });

export const llmSettingsQuery = () => queryOptions({ queryKey: ["settings", "llm"], queryFn: () => api.settings.llm() });

export const notificationSettingsQuery = (clientId: string) =>
  queryOptions({ queryKey: [...clientKey(clientId), "notification-settings"], queryFn: () => api.settings.notifications(clientId) });
