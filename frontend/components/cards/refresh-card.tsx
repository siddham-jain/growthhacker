"use client";

import { RiExternalLinkLine, RiFileCopyLine } from "@remixicon/react";
import { useState } from "react";
import { InfoNote } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import type { InsertionOp, InsertionPosition, RefreshSuggestionView } from "@/lib/api/types";
import { formatCompact, formatDate, formatExact } from "@/lib/format";

const POSITION_LABELS: Record<InsertionPosition, string> = {
  top_line: "First line",
  after_first_paragraph: "After the first paragraph",
  before_hashtag_line: "Before the hashtags",
  before_links_block: "Before the links",
  append: "At the end",
};

const DESCRIPTION_LIMIT_BYTES = 5_000;

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="secondary"
      size="xs"
      leadingIcon={RiFileCopyLine}
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
      }}
    >
      {copied ? "Copied" : "Copy line"}
    </Button>
  );
}

// insert-only diff: added lines get a + gutter; only link fixes ever show removed text
function OperationRow({ operation }: { operation: InsertionOp }) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-caption-1-medium text-muted-foreground">{POSITION_LABELS[operation.position]}</p>
      <p className="truncate text-caption-1-regular text-subtle-foreground" dir="auto">
        {operation.anchor_excerpt}
      </p>
      {operation.kind === "link_fix" && operation.old_text ? (
        <p className="flex gap-2 font-mono text-caption-1-regular text-danger">
          <span aria-hidden>−</span>
          <span className="line-through">{operation.old_text}</span>
        </p>
      ) : null}
      <div className="flex items-start justify-between gap-3 rounded-sm border-l-2 border-success bg-[color-mix(in_oklab,var(--success)_6%,var(--card))] px-3 py-2">
        <p className="flex gap-2 text-body-regular whitespace-pre-wrap text-foreground" dir="auto">
          <span aria-hidden className="text-success">+</span>
          <span>{operation.text}</span>
        </p>
        <CopyButton text={operation.text} />
      </div>
    </div>
  );
}

export function RefreshCard({ suggestion }: { suggestion: RefreshSuggestionView }) {
  const usage = suggestion.resulting_bytes / DESCRIPTION_LIMIT_BYTES;
  return (
    <article className="flex flex-col gap-3 rounded-card bg-card p-4 shadow-card sm:p-5">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-headline text-foreground">{suggestion.video.title}</h3>
          <p className="text-caption-1-regular text-muted-foreground">
            published {formatDate(suggestion.video.published_at)} · {formatCompact(suggestion.video.views)} views
          </p>
        </div>
        <a href={suggestion.video.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-body-medium text-info">
          Open on YouTube <RiExternalLinkLine aria-hidden className="size-4" />
        </a>
      </header>
      <p className="text-body-regular text-foreground">
        <span className="text-muted-foreground">Why now: </span>
        {suggestion.warranted_because.join(" ")}
      </p>
      <div className="flex flex-col gap-3 rounded-control border border-border p-3">
        {suggestion.operations.map((operation) => (
          <OperationRow key={operation.op_id} operation={operation} />
        ))}
      </div>
      <p className="text-caption-1-regular text-muted-foreground">
        Adds {suggestion.inserted_bytes} bytes ({formatExact(suggestion.resulting_bytes - suggestion.inserted_bytes)} → {formatExact(suggestion.resulting_bytes)} of{" "}
        {formatExact(DESCRIPTION_LIMIT_BYTES)}){usage > 0.9 ? " · close to YouTube's limit" : ""}
      </p>
      <p className="text-body-regular text-muted-foreground">{suggestion.expected_effect}</p>
      {suggestion.review_required ? <InfoNote tone="warning">Needs review before you paste.</InfoNote> : null}
    </article>
  );
}
