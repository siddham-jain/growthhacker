"use client";

import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useCallback, useMemo, useRef } from "react";
import { toast } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { isApiError } from "@/lib/api/errors";
import { buyerQuestionsQuery, clientQuery, competitorsQuery, keywordsQuery, profileQuery } from "@/lib/api/queries";
import type { BusinessProfileView, BuyerQuestionItem, CompetitorAddIn, CompetitorRole, CompetitorView, KeywordItem, Page, ProfilePatch } from "@/lib/api/types";
import { applyProfileView, applyTopicEdit, upsertById, type TopicEdit } from "./edits";

function problemMessage(error: unknown, fallback: string): string {
  return isApiError(error) && error.status < 500 && error.message ? error.message : fallback;
}

// every correction is saved at once; profile edits run one after another because each needs the version the last one made
export function useProfileEdits(clientId: string) {
  const queryClient = useQueryClient();
  const key = profileQuery(clientId).queryKey;
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const pending = useRef<ProfilePatch[]>([]);

  return useCallback(
    (patch: ProfilePatch) => {
      pending.current.push(patch);
      queryClient.setQueryData<BusinessProfileView>(key, (view) => (view ? applyProfileView(view, patch) : view));
      const run = queue.current.then(async () => {
        try {
          const version = queryClient.getQueryData<BusinessProfileView>(key)?.version ?? 0;
          const saved = await api.profile.update(clientId, patch, version);
          pending.current = pending.current.filter((item) => item !== patch);
          // edits still waiting in the queue stay visible on top of the server's copy
          queryClient.setQueryData(key, pending.current.reduce(applyProfileView, saved));
          if (patch.identity?.country) queryClient.invalidateQueries({ queryKey: clientQuery(clientId).queryKey });
        } catch (error) {
          pending.current = pending.current.filter((item) => item !== patch);
          await queryClient.invalidateQueries({ queryKey: key });
          toast.error(isApiError(error) && error.status === 412 ? "Someone else changed this" : "Couldn't save that", {
            description: isApiError(error) && error.status === 412 ? "We loaded the latest version. Re-apply your edit." : problemMessage(error, "Try again."),
          });
        }
      });
      queue.current = run;
      return run;
    },
    [clientId, key, queryClient],
  );
}

type TopicKind = "keywords" | "buyer-questions";

export function useTopicEdits(clientId: string, kind: TopicKind) {
  const queryClient = useQueryClient();
  const resource = kind === "keywords" ? api.keywords : api.buyerQuestions;
  const key: QueryKey = (kind === "keywords" ? keywordsQuery(clientId) : buyerQuestionsQuery(clientId)).queryKey;

  return useMemo(() => {
    type Item = KeywordItem | BuyerQuestionItem;
    const local = (edit: TopicEdit) => queryClient.setQueryData<Page<Item>>(key, (page) => (page ? applyTopicEdit(page, edit) : page));
    const save = async (edit: TopicEdit, call: () => Promise<Item | void | { updated: number }>, replaceId?: string) => {
      local(edit);
      try {
        const result = await call();
        if (result && "id" in result) queryClient.setQueryData<Page<Item>>(key, (page) => (page ? upsertById(page, result, replaceId ?? result.id) : page));
      } catch (error) {
        await queryClient.invalidateQueries({ queryKey: key });
        toast.error("Couldn't save that", { description: problemMessage(error, "Try again.") });
      }
    };
    return {
      remove: (id: string) => save({ op: "remove", id }, () => resource.remove(clientId, id)),
      // a restored keyword comes back under the id of its text, so the old row is replaced
      restore: (id: string) => save({ op: "restore", id }, () => resource.update(clientId, id, { status: "confirmed" }), id),
      edit: (id: string, text: string) => save({ op: "edit", id, text }, () => resource.update(clientId, id, { text })),
      confirm: (ids: string[]) => (ids.length ? save({ op: "confirm", ids }, () => resource.bulkStatus(clientId, { ids, status: "confirmed" })) : Promise.resolve()),
      add: async (text: string): Promise<string | null> => {
        try {
          const created = kind === "keywords" ? await api.keywords.create(clientId, { text, language: "en", weight: 1 }) : await api.buyerQuestions.create(clientId, { text, language: "en", priority: 2 });
          queryClient.setQueryData<Page<Item>>(key, (page) => (page ? upsertById(page, created) : page));
          return null;
        } catch (error) {
          if (isApiError(error) && error.status === 409) return "That's already in the list.";
          return problemMessage(error, "Couldn't add that. Try again.");
        }
      },
    };
  }, [clientId, key, kind, queryClient, resource]);
}

export function useCompetitorEdits(clientId: string) {
  const queryClient = useQueryClient();
  const key = competitorsQuery(clientId).queryKey;

  return useMemo(() => {
    const patchLocal = (id: string, change: Partial<CompetitorView>) =>
      queryClient.setQueryData<Page<CompetitorView>>(key, (page) => (page ? { ...page, items: page.items.map((item) => (item.id === id ? { ...item, ...change } : item)) } : page));
    const save = async (id: string, change: Partial<CompetitorView>, call: () => Promise<CompetitorView>) => {
      patchLocal(id, change);
      try {
        const saved = await call();
        queryClient.setQueryData<Page<CompetitorView>>(key, (page) => (page ? upsertById(page, saved) : page));
      } catch (error) {
        await queryClient.invalidateQueries({ queryKey: key });
        toast.error("Couldn't save that", { description: problemMessage(error, "Try again.") });
      }
    };
    return {
      remove: (item: CompetitorView) => save(item.id, { status: "rejected" }, () => api.competitors.reject(clientId, item.id, { reason: "not_competitor" })),
      restore: (item: CompetitorView) => save(item.id, { status: "confirmed" }, () => api.competitors.confirm(clientId, item.id)),
      move: (item: CompetitorView, role: CompetitorRole) => save(item.id, { role }, () => api.competitors.move(clientId, item.id, role)),
      confirm: (items: CompetitorView[]) => Promise.all(items.map((item) => save(item.id, { status: "confirmed" }, () => api.competitors.confirm(clientId, item.id)))),
      add: async (channelRef: string, role: CompetitorAddIn["role"]): Promise<string | null> => {
        try {
          const added = await api.competitors.add(clientId, { channel_ref: channelRef, role }, crypto.randomUUID());
          queryClient.setQueryData<Page<CompetitorView>>(key, (page) => (page ? upsertById(page, added) : page));
          return null;
        } catch (error) {
          return problemMessage(error, "We couldn't add that. Paste the channel link (youtube.com/@…).");
        }
      },
    };
  }, [clientId, key, queryClient]);
}
