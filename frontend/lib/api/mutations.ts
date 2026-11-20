"use client";

import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { api } from "./index";
import type { RecommendationsResult } from "./contract";
import { clientKey } from "./queries";
import type { FeedbackIn, HygieneData, NewsOpportunityView, Page, RecommendationOut, RefreshSuggestionView, ResultEnvelope } from "./types";

type FeedbackInput = { recommendationId: string } & FeedbackIn;

function withFeedback(result: RecommendationsResult, input: FeedbackInput): RecommendationsResult {
  if (result.state !== "ready") return result;
  const items = result.envelope.data.items.map((item): RecommendationOut =>
    item.id === input.recommendationId ? { ...item, feedback: { status: input.status ?? null, reason: input.reason ?? null, linked_video_id: input.linked_video_id ?? null } } : item,
  );
  return { ...result, envelope: { ...result.envelope, data: { ...result.envelope.data, items } } };
}

// optimistic: the card updates at once and rolls back if the server refuses
export function useRecommendationFeedback(clientId: string) {
  const queryClient = useQueryClient();
  const listKey = [...clientKey(clientId), "recommendations"];
  return useMutation({
    mutationKey: ["feedback", clientId],
    mutationFn: async ({ recommendationId, ...input }: FeedbackInput) => api.recommendations.feedback(recommendationId, input, crypto.randomUUID()),
    onMutate: async (input) => {
      await queryClient.cancelQueries({ queryKey: listKey });
      const snapshot = queryClient.getQueriesData<RecommendationsResult>({ queryKey: listKey });
      queryClient.setQueriesData<RecommendationsResult>({ queryKey: listKey }, (current) => (current ? withFeedback(current, input) : current));
      return { snapshot };
    },
    onError: (_error, _input, context) => {
      context?.snapshot.forEach(([key, value]) => queryClient.setQueryData(key, value));
    },
  });
}

type ListEnvelope<T> = ResultEnvelope<Page<T>>;

function replaceItem<T extends { id: string }>(queryClient: ReturnType<typeof useQueryClient>, key: QueryKey, next: T) {
  queryClient.setQueryData<ListEnvelope<T>>(key, (current) =>
    current ? { ...current, data: { ...current.data, items: current.data.items.map((item) => (item.id === next.id ? next : item)) } } : current,
  );
}

export function useDismissNews(clientId: string) {
  const queryClient = useQueryClient();
  const key = [...clientKey(clientId), "news"];
  return useMutation({
    mutationFn: async (opportunityId: string) => api.news.dismiss(opportunityId),
    onSuccess: (next) => replaceItem<NewsOpportunityView>(queryClient, key, next),
  });
}

export function useRefreshAction(clientId: string) {
  const queryClient = useQueryClient();
  const key = [...clientKey(clientId), "refresh"];
  return useMutation({
    mutationFn: async ({ id, action, appliedText }: { id: string; action: "applied" | "dismiss"; appliedText?: string }) =>
      action === "applied" ? api.refresh.applied(id, { applied_at: new Date().toISOString(), applied_text: appliedText }) : api.refresh.dismiss(id),
    onSuccess: (next) => {
      replaceItem<RefreshSuggestionView>(queryClient, key, next);
      queryClient.setQueryData<ResultEnvelope<HygieneData>>([...clientKey(clientId), "hygiene"], (current) =>
        current ? { ...current, data: { ...current.data, items: current.data.items.map((item) => (item.id === next.id ? next : item)) } } : current,
      );
    },
  });
}
