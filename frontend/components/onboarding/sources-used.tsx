"use client";

import { RiArrowDownSLine, RiForbidLine } from "@remixicon/react";
import { useId, useState } from "react";
import { SourceKindChip } from "@/components/ui/chips";
import type { ContextSourceKind, ProfileSource } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { formatDate } from "@/lib/format";
import { SOURCE_KIND_LABELS } from "@/lib/labels";
import { groupSources, isBlockedPage, pageLabel } from "@/lib/sources";

function plural(count: number, word: string): string {
  return `${count} ${count === 1 ? word : `${word}s`}`;
}

// the acquisition cascade made visible: blocked sources stay listed so the routing is never hidden
export function SourcesUsed({ sources }: { sources: ProfileSource[] }) {
  const [open, setOpen] = useState<ContextSourceKind | null>(null);
  const panelId = useId();
  const groups = groupSources(sources);
  const expanded = groups.find((group) => group.kind === open);

  return (
    <section aria-labelledby={`${panelId}-title`} className="flex flex-col gap-2">
      <h3 id={`${panelId}-title`} className="text-eyebrow text-muted-foreground">
        Sources used
      </h3>
      <ul className="flex flex-wrap gap-2">
        {groups.map((group) => (
          <li key={group.kind}>
            <button
              type="button"
              aria-expanded={open === group.kind}
              aria-controls={panelId}
              onClick={() => setOpen(open === group.kind ? null : group.kind)}
              className={cx(
                "flex items-center gap-1.5 rounded-full border bg-card py-1 pr-2 pl-1 text-caption-1-regular text-muted-foreground transition-colors",
                open === group.kind ? "border-foreground" : "border-border hover:border-border-strong",
              )}
            >
              <SourceKindChip kind={group.kind} className="rounded-full" />
              {group.blocked ? (
                <span className="inline-flex items-center gap-1 text-danger">
                  <RiForbidLine aria-hidden className="size-3.5" /> blocked, routed around
                </span>
              ) : (
                <span className="tabular-nums">
                  {plural(group.pages.length, "page")} · {plural(group.facts, "fact")}
                </span>
              )}
              <RiArrowDownSLine aria-hidden className={cx("size-4 transition-transform", open === group.kind && "rotate-180")} />
            </button>
          </li>
        ))}
      </ul>
      {expanded ? (
        <div id={panelId} className="animate-reveal rounded-card border border-border bg-card p-3">
          <p className="text-caption-1-regular text-muted-foreground">
            {SOURCE_KIND_LABELS[expanded.kind].description} · {plural(expanded.pages.length, "page")}
          </p>
          <ul className="mt-2 max-h-64 divide-y divide-border overflow-y-auto">
            {expanded.pages.map((page) => (
              <li key={page.url} className="flex items-center justify-between gap-3 py-1.5 text-caption-1-regular">
                <a href={page.url} target="_blank" rel="noreferrer" title={page.url} className="min-w-0 truncate text-foreground underline decoration-border-strong underline-offset-4 hover:decoration-foreground">
                  {pageLabel(page.url)}
                </a>
                <span className={cx("shrink-0 tabular-nums", isBlockedPage(page) ? "text-danger" : "text-muted-foreground")}>
                  {isBlockedPage(page) ? "blocked" : plural(page.claims, "fact")}
                  {page.snapshot_at ? ` · snapshot ${formatDate(page.snapshot_at)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
