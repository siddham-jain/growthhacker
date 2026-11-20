"use client";

import { RiAddLine, RiCheckLine, RiCloseLine } from "@remixicon/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Chip, SegmentedControl, SegmentedControlItem, toast } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { buyerQuestionsQuery, keywordsQuery } from "@/lib/api/queries";
import type { BuyerQuestionItem, KeywordItem, TopicStatus } from "@/lib/api/types";
import { SettingsSection } from "./section";

type Kind = "keywords" | "questions";
type Item = KeywordItem | BuyerQuestionItem;

const SOURCE_LABELS: Record<string, string> = {
  profile: "From your profile",
  website_faq: "From your FAQ",
  website_derived: "From your website",
  user: "Added by you",
  spotlit: "Tracking",
  comments: "From comments",
  import: "Imported",
};
const STAGE_LABELS: Record<BuyerQuestionItem["funnel_stage"], string> = { awareness: "Awareness", consideration: "Considering", decision: "Ready to buy", post_purchase: "After buying" };
const STATUS_TABS: { id: TopicStatus; label: string }[] = [
  { id: "confirmed", label: "Tracking" },
  { id: "proposed", label: "Suggested" },
  { id: "rejected", label: "Rejected" },
  { id: "retired", label: "Retired" },
];

function useTopicList(clientId: string, kind: Kind, items: Item[], onChanged: () => void) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<TopicStatus>("confirmed");
  const [draft, setDraft] = useState("");
  // new topics start at the backend defaults: full weight for keywords, middle priority (1–3) for questions
  const create = (text: string) =>
    kind === "keywords" ? api.keywords.create(clientId, { text, weight: 1, language: "en" }) : api.buyerQuestions.create(clientId, { text, priority: 2, language: "en", funnel_stage: null });
  const topicApi = kind === "keywords" ? api.keywords : api.buyerQuestions;
  const listKey = kind === "keywords" ? keywordsQuery(clientId).queryKey : buyerQuestionsQuery(clientId).queryKey;
  const refresh = () => queryClient.invalidateQueries({ queryKey: listKey });

  const setItemStatus = useMutation({
    mutationFn: async ({ id, next }: { id: string; next: "confirmed" | "rejected" }) => topicApi.bulkStatus(clientId, { ids: [id], status: next }),
    onSuccess: () => {
      refresh();
      onChanged();
    },
    onError: () => toast.error("Couldn't update that. Try again."),
  });
  const add = useMutation({
    mutationFn: async (text: string) => create(text),
    onSuccess: () => {
      setDraft("");
      refresh();
      onChanged();
      toast.success(kind === "keywords" ? "Keyword added" : "Question added");
    },
    onError: () => toast.error("Couldn't add that. Try again."),
  });

  const counts: Record<TopicStatus, number> = { confirmed: 0, proposed: 0, rejected: 0, retired: 0 };
  items.forEach((item) => (counts[item.status] += 1));
  return { shown: items.filter((item) => item.status === status), counts, status, setStatus, draft, setDraft, setItemStatus, add };
}

function TopicSection({ clientId, kind, items, activeTotal, onChanged }: { clientId: string; kind: Kind; items: Item[]; activeTotal: number; onChanged: () => void }) {
  const { shown, counts, status, setStatus, draft, setDraft, setItemStatus, add } = useTopicList(clientId, kind, items, onChanged);
  const noun = kind === "keywords" ? "keyword" : "buyer question";
  const lastActive = activeTotal <= 1;
  return (
    <SettingsSection
      title={kind === "keywords" ? "Keywords" : "Buyer questions"}
      description={kind === "keywords" ? "Words your buyers search for. We match videos to these." : "What customers ask before they buy. Ideas that answer these earn the Conversion lens."}
    >
      <SegmentedControl aria-label={`${noun} status`} selectedKeys={[status]} onSelectionChange={(keys) => setStatus([...keys][0] as TopicStatus)}>
        {STATUS_TABS.filter((tab) => tab.id !== "retired" || counts.retired).map((tab) => (
          <SegmentedControlItem key={tab.id} id={tab.id}>
            {tab.label} · {counts[tab.id]}
          </SegmentedControlItem>
        ))}
      </SegmentedControl>
      <ul className="-mt-1 divide-y divide-border">
        {shown.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center gap-2 py-2">
            <span className="min-w-0 flex-1 basis-56 text-body-regular text-foreground" dir="auto">
              {item.text}
            </span>
            {"funnel_stage" in item ? (
              <Chip variant="caption" color="soft">
                {STAGE_LABELS[item.funnel_stage]}
              </Chip>
            ) : null}
            {"priority" in item ? (
              <Chip variant="caption" color="soft" title="Priority 1 is highest">
                Priority {item.priority}
              </Chip>
            ) : null}
            <Chip variant="caption" color="gray">
              {SOURCE_LABELS[item.source] ?? item.source}
            </Chip>
            {item.status !== "confirmed" ? (
              <Button variant="secondary" size="xs" leadingIcon={RiCheckLine} onClick={() => setItemStatus.mutate({ id: item.id, next: "confirmed" })}>
                Track
              </Button>
            ) : null}
            {item.status !== "rejected" ? (
              <Button
                variant="ghost"
                size="xs"
                leadingIcon={RiCloseLine}
                disabled={item.status === "confirmed" && lastActive}
                title={item.status === "confirmed" && lastActive ? "Keep at least one keyword or buyer question so we can match videos" : undefined}
                onClick={() => setItemStatus.mutate({ id: item.id, next: "rejected" })}
              >
                {item.status === "confirmed" ? "Stop tracking" : "Reject"}
              </Button>
            ) : null}
          </li>
        ))}
        {!shown.length ? <li className="py-3 text-body-regular text-muted-foreground">Nothing here.</li> : null}
      </ul>
      {lastActive ? <p className="text-caption-1-regular text-muted-foreground">Keep at least one keyword or buyer question so we can match videos to you.</p> : null}
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (draft.trim()) add.mutate(draft.trim());
        }}
      >
        <label className="sr-only" htmlFor={`add-${kind}`}>
          Add a {noun}
        </label>
        <input
          id={`add-${kind}`}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={kind === "keywords" ? "Add a keyword, e.g. heart surgery cost" : "Add a question, e.g. How long is recovery?"}
          className="h-9 min-w-0 flex-1 rounded-control border border-dashed border-border-strong bg-card px-3 text-body-regular outline-none placeholder:text-subtle-foreground focus-visible:border-ring"
        />
        <Button type="submit" variant="secondary" size="xs" leadingIcon={RiAddLine} disabled={add.isPending || !draft.trim()}>
          Add
        </Button>
      </form>
    </SettingsSection>
  );
}

export function TopicsEditor({ clientId, keywords, questions, onChanged }: { clientId: string; keywords: KeywordItem[]; questions: BuyerQuestionItem[]; onChanged: () => void }) {
  const activeTotal = [...keywords, ...questions].filter((item) => item.status === "confirmed").length;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <TopicSection clientId={clientId} kind="questions" items={questions} activeTotal={activeTotal} onChanged={onChanged} />
      <TopicSection clientId={clientId} kind="keywords" items={keywords} activeTotal={activeTotal} onChanged={onChanged} />
    </div>
  );
}
