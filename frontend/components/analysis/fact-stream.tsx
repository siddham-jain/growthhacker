"use client";

import { ContextCards, type ContextChunk } from "@/components/ui/primitives";
import type { ContextSourceKind } from "@/lib/api/types";
import { PROFILE_FIELD_LABELS, SOURCE_KIND_LABELS } from "@/lib/labels";
import type { LiveRunState } from "@/lib/runs/run-reducer";

const BADGE_TONE: Record<ContextSourceKind, string> = {
  site_direct: "bg-info",
  site_rendered: "bg-info",
  reader_proxy: "bg-lens-conversion",
  search_index: "bg-lens-conversion",
  archive: "bg-warning",
  knowledge_base: "bg-chart-3",
  web_mentions: "bg-warning",
  youtube_channel: "bg-danger",
  user: "bg-foreground",
};

export function FactStream({ state }: { state: LiveRunState }) {
  if (!state.facts.length) return null;
  const chunks: ContextChunk[] = state.facts.map((fact) => {
    const kinds = fact.source_kinds ?? [];
    const lead = kinds[0] ?? "site_direct";
    return {
      title: PROFILE_FIELD_LABELS[fact.field] ?? fact.field,
      chars: `${fact.count} found`,
      body: fact.preview.join(" · "),
      source: kinds.length ? `From ${kinds.map((kind) => SOURCE_KIND_LABELS[kind].label.toLowerCase()).join(", ")}` : "From your website",
      badge: SOURCE_KIND_LABELS[lead].short.slice(0, 3).toUpperCase(),
      tone: BADGE_TONE[lead],
    };
  });
  return <ContextCards chunks={chunks} labels={{ header: "What we learned", count: String(chunks.length) }} className="max-w-none" />;
}
