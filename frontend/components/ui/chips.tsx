import type { ContextSourceKind, Lens, RecType } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { LENS_LABELS, REC_TYPE_LABELS, SOURCE_KIND_LABELS } from "@/lib/labels";
import { Chip } from "./primitives";

export function TypeChip({ type }: { type: RecType }) {
  return (
    <Chip variant="caption" color="gray">
      {REC_TYPE_LABELS[type]}
    </Chip>
  );
}

export function LensChip({ lens, emphasised = false }: { lens: Lens; emphasised?: boolean }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-caption-1-medium",
        lens === "views" ? "text-lens-views" : "text-lens-conversion",
        emphasised ? "bg-muted ring-1 ring-current/25" : "bg-transparent",
      )}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {LENS_LABELS[lens]}
    </span>
  );
}

const SOURCE_TONE: Record<ContextSourceKind, "blue" | "cyan" | "orange" | "purple" | "gray" | "rose"> = {
  site_direct: "blue",
  site_rendered: "blue",
  reader_proxy: "cyan",
  search_index: "cyan",
  archive: "orange",
  knowledge_base: "purple",
  web_mentions: "orange",
  youtube_channel: "rose",
  user: "gray",
};

export function SourceKindChip({ kind, className }: { kind: ContextSourceKind; className?: string }) {
  const label = SOURCE_KIND_LABELS[kind];
  return (
    <Chip variant="caption" color={SOURCE_TONE[kind]} className={className} title={label.description}>
      {label.label}
    </Chip>
  );
}

// youtube-derived, website and news numbers are never merged; every metric names its origin
export function OriginChip({ origin }: { origin: "YouTube public" | "YouTube Analytics (owner)" | "Website" | "News" }) {
  return (
    <Chip variant="caption" color="soft">
      {origin}
    </Chip>
  );
}
