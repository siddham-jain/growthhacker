import type { BusinessProfile, BusinessProfileView, BuyerQuestionItem, CompetitorView, KeywordItem, Page, ProfileFact, ProfilePatch } from "@/lib/api/types";
import { isTracked } from "@/lib/competitors";
import { SOURCE_KIND_LABELS } from "@/lib/labels";

export const MIN_COMPETITORS = 3;
export const MAX_COMPETITORS = 10;
// long lists open on this many items and offer "show all"
export const PREVIEW_COUNT = 8;
const LOW_CONFIDENCE = 0.6;

export function newItemId(): string {
  return `user_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
}

// a reason to look twice: read from someone else's site or an archive, or extracted with low confidence
export function factCheckReason(fact: ProfileFact): string | null {
  if (fact.meta.origin === "user" || fact.meta.status === "edited") return null;
  const thirdParty = fact.meta.evidence.find((evidence) => SOURCE_KIND_LABELS[evidence.source_kind].thirdParty);
  if (thirdParty) return `From ${SOURCE_KIND_LABELS[thirdParty.source_kind].label.toLowerCase()}, not your site`;
  if (fact.meta.status === "needs_confirmation" || fact.meta.status === "stale") return "We couldn't confirm this on your site";
  if (fact.meta.confidence < LOW_CONFIDENCE) return "Low confidence";
  return null;
}

export function keywordCheckReason(keyword: KeywordItem): string | null {
  return keyword.source === "llm_expansion" ? "Suggested by us, not found on your site" : null;
}

export function questionCheckReason(question: BuyerQuestionItem): string | null {
  return question.source === "spotlit" || question.source === "comments" || question.source === "import" ? "From outside your site" : null;
}

export function competitorCheckReason(competitor: CompetitorView): string | null {
  if (competitor.added_by === "user") return null;
  const ownership = competitor.channels?.find((channel) => channel.is_primary)?.ownership.label;
  if (ownership === "uncertain") return "We're not sure this channel is theirs";
  return competitor.confidence === "low" ? "Weak match with what you sell" : null;
}

// flagged items first so they are checked while attention is high; the order within each group is kept
export function checkFirst<T>(items: T[], reason: (item: T) => string | null): T[] {
  return [...items.filter((item) => reason(item) !== null), ...items.filter((item) => reason(item) === null)];
}

// pinned items (the ones just added) stay visible even when the list is collapsed
export function previewItems<T>(items: T[], expanded: boolean, pinned: (item: T) => boolean = () => false, limit = PREVIEW_COUNT): { shown: T[]; hidden: number } {
  if (expanded || items.length <= limit + 2) return { shown: items, hidden: 0 };
  const shown = items.filter((item, index) => index < limit || pinned(item));
  return { shown, hidden: items.length - shown.length };
}

function userFact(id: string, label: string): ProfileFact {
  return { id, label, meta: { origin: "user", status: "confirmed", confidence: 1, evidence: [] } };
}

// the optimistic copy of a profile edit; the server's answer replaces it
export function applyProfilePatch(profile: BusinessProfile, patch: ProfilePatch): BusinessProfile {
  const next: BusinessProfile = { ...profile, identity: { ...profile.identity, ...patch.identity } };
  for (const edit of patch.facts ?? []) {
    const facts = next[edit.group];
    if (edit.op === "add") next[edit.group] = [...facts, userFact(edit.id, edit.label)];
    else if (edit.op === "restore") next[edit.group] = facts.some((fact) => fact.id === edit.fact.id) ? facts : [...facts, edit.fact];
    else if (edit.op === "remove") next[edit.group] = facts.filter((fact) => fact.id !== edit.id);
    else if (edit.op === "edit") next[edit.group] = facts.map((fact) => (fact.id === edit.id ? { ...fact, label: edit.label, meta: { ...fact.meta, origin: "user", status: "edited" } } : fact));
    else next[edit.group] = facts.map((fact) => (fact.id === edit.id ? { ...fact, meta: { ...fact.meta, status: "confirmed" } } : fact));
  }
  return next;
}

export function applyProfileView(view: BusinessProfileView, patch: ProfilePatch): BusinessProfileView {
  return { ...view, effective: applyProfilePatch(view.effective, patch) };
}

export type TopicEdit = { op: "remove"; id: string } | { op: "restore"; id: string } | { op: "edit"; id: string; text: string } | { op: "confirm"; ids: string[] };

export function applyTopicEdit<T extends { id: string; text: string; status: string }>(page: Page<T>, edit: TopicEdit): Page<T> {
  const items = page.items.map((item) => {
    if (edit.op === "confirm") return edit.ids.includes(item.id) ? { ...item, status: "confirmed" } : item;
    if (item.id !== edit.id) return item;
    if (edit.op === "remove") return { ...item, status: "rejected" };
    if (edit.op === "restore") return { ...item, status: "confirmed" };
    return { ...item, text: edit.text };
  });
  return { ...page, items };
}

export function activeTopics<T extends { status: string }>(items: T[]): T[] {
  return items.filter((item) => item.status === "proposed" || item.status === "confirmed");
}

export function upsertById<T extends { id: string }>(page: Page<T>, item: T, replaceId = item.id): Page<T> {
  const exists = page.items.some((current) => current.id === replaceId);
  return { ...page, items: exists ? page.items.map((current) => (current.id === replaceId ? item : current)) : [...page.items, item] };
}

export interface CompetitorGroups {
  direct: CompetitorView[];
  watch: CompetitorView[];
  // also-watch candidates with no youtube channel: nothing to track yet, so they stay out of the list
  noChannel: CompetitorView[];
}

export function groupCompetitors(items: CompetitorView[]): CompetitorGroups {
  const live = items.filter((item) => item.role !== "excluded" && item.status !== "rejected" && item.status !== "excluded");
  return {
    direct: live.filter((item) => item.role === "competitor_set" && item.status !== "no_channel"),
    watch: live.filter((item) => item.role !== "competitor_set" && item.status !== "no_channel"),
    noChannel: live.filter((item) => item.status === "no_channel"),
  };
}

export function competitorBlocker(direct: number): string | null {
  if (direct < MIN_COMPETITORS) return `Keep at least ${MIN_COMPETITORS} direct competitors. Add one below, or move one up from “Also watch”.`;
  if (direct > MAX_COMPETITORS) return `You can track up to ${MAX_COMPETITORS}. Move extras to “Also watch”.`;
  return null;
}

export function untracked(items: CompetitorView[]): CompetitorView[] {
  return items.filter((item) => !isTracked(item));
}
