"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./index";
import type { RecommendationsResult } from "./contract";
import { clientKey } from "./queries";
import type { FeedbackStatus } from "./types";

type FeedbackInput = { recommendationId: string; status: FeedbackStatus | null };

function withStatus(result: RecommendationsResult, recommendationId: string, status: FeedbackStatus | null): RecommendationsResult {
  if (result.state !== "ready") return result;
  const items = result.envelope.data.items.map((item) =>
    item.id === recommendationId ? { ...item, feedback: { rating: item.feedback?.rating ?? null, status } } : item,
  );
  return { ...result, envelope: { ...result.envelope, data: { ...result.envelope.data, items } } };
}

// optimistic: the card updates at once and rolls back if the server refuses
export function useRecommendationFeedback(clientId: string) {
  const queryClient = useQueryClient();
  const listKey = [...clientKey(clientId), "recommendations"];
  return useMutation({
    mutationFn: ({ recommendationId, status }: FeedbackInput) =>
      api.recommendations.feedback(recommendationId, status ? { status } : {}, crypto.randomUUID()),
    onMutate: async ({ recommendationId, status }) => {
      await queryClient.cancelQueries({ queryKey: listKey });
      const snapshot = queryClient.getQueriesData<RecommendationsResult>({ queryKey: listKey });
      queryClient.setQueriesData<RecommendationsResult>({ queryKey: listKey }, (current) =>
        current ? withStatus(current, recommendationId, status) : current,
      );
      return { snapshot };
    },
    onError: (_error, _input, context) => {
      context?.snapshot.forEach(([key, value]) => queryClient.setQueryData(key, value));
    },
  });
}
