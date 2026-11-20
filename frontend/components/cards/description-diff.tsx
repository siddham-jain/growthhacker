"use client";

import { RiFileCopyLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/primitives";
import type { InsertionPosition } from "@/lib/api/types";
import { copyText } from "@/lib/clipboard";
import { highlightKeywords, type DiffRow } from "@/lib/refresh-diff";

export const POSITION_LABELS: Record<InsertionPosition, string> = {
  top_line: "First line",
  after_first_paragraph: "After the first paragraph",
  before_hashtag_line: "Before the hashtags",
  before_links_block: "Before the links",
  append: "At the end",
};

export async function copyWithToast(text: string, what = "Copied") {
  const ok = await copyText(text);
  if (ok) toast.success(what, { description: "Paste it in YouTube Studio → Details → Description." });
  else toast.error("Your browser blocked copying", { description: "Select the text and press ⌘C (Ctrl+C) instead." });
}

function Gap({ lines }: { lines: number }) {
  return <li className="py-1 pl-7 font-mono text-caption-1-regular text-subtle-foreground">⋯ {lines} unchanged {lines === 1 ? "line" : "lines"} ⋯</li>;
}

function ContextLine({ text }: { text: string }) {
  return (
    <li className="flex gap-3 py-0.5 pl-2 text-body-regular text-muted-foreground">
      <span aria-hidden className="w-3 shrink-0 select-none font-mono text-subtle-foreground" />
      <span className="min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]" dir="auto">
        {text || " "}
      </span>
    </li>
  );
}

function ChangedLine({ row }: { row: Extract<DiffRow, { kind: "insert" | "link_fix" }> }) {
  return (
    <li className="my-1 flex flex-col gap-1">
      {row.kind === "link_fix" ? (
        <p className="flex gap-3 rounded-sm bg-[color-mix(in_oklab,var(--danger)_6%,var(--card))] py-1 pl-2 text-body-regular text-danger">
          <span aria-hidden className="w-3 shrink-0 select-none font-mono">−</span>
          <span className="sr-only">Replace: </span>
          <span className="min-w-0 line-through [overflow-wrap:anywhere]">{row.oldText}</span>
        </p>
      ) : (
        row.op ? <p className="pl-7 text-caption-1-medium text-success">{POSITION_LABELS[row.op.position]}</p> : null
      )}
      <div className="flex items-start justify-between gap-3 rounded-sm border-l-2 border-success bg-[color-mix(in_oklab,var(--success)_8%,var(--card))] py-1.5 pr-1.5 pl-2">
        <p className="flex min-w-0 gap-3 text-body-regular text-foreground">
          <span aria-hidden className="w-3 shrink-0 select-none font-mono text-success">+</span>
          <span className="sr-only">Add: </span>
          <span className="min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]" dir="auto">
            {highlightKeywords(row.text, row.op?.keywords_added ?? []).map((part, index) =>
              part.mark ? (
                <mark key={index} className="rounded-sm bg-[color-mix(in_oklab,var(--success)_22%,var(--card))] px-0.5 text-foreground">
                  {part.text}
                </mark>
              ) : (
                <span key={index}>{part.text}</span>
              ),
            )}
          </span>
        </p>
        <Button variant="secondary" size="xs" leadingIcon={RiFileCopyLine} onClick={() => copyWithToast(row.text, "Line copied")}>
          Copy line
        </Button>
      </div>
    </li>
  );
}

// the description as it reads today with the additions spliced in; nothing existing is ever drawn as deleted
export function DescriptionDiff({ rows }: { rows: DiffRow[] }) {
  return (
    <ol aria-label="Description with suggested additions" className="flex flex-col rounded-control border border-border bg-card px-2 py-2">
      {rows.map((row, index) =>
        row.kind === "gap" ? <Gap key={index} lines={row.lines} /> : row.kind === "context" ? <ContextLine key={index} text={row.text} /> : <ChangedLine key={index} row={row} />,
      )}
    </ol>
  );
}
