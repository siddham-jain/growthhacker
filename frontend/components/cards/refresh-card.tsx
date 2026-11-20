"use client";

import { RiCheckDoubleLine, RiCloseLine, RiExternalLinkLine, RiFileCopyLine } from "@remixicon/react";
import { InfoNote } from "@/components/states/states";
import { Button } from "@/components/ui/button";
import { Chip, Meter } from "@/components/ui/primitives";
import type { RefreshSuggestionView } from "@/lib/api/types";
import { formatDate, formatExact } from "@/lib/format";
import { addedText, parseUnifiedDiff } from "@/lib/refresh-diff";
import { copyWithToast, DescriptionDiff, POSITION_LABELS } from "./description-diff";

const DESCRIPTION_LIMIT_BYTES = 5_000;

interface RefreshCardProps {
  suggestion: RefreshSuggestionView;
  onApplied: (appliedText: string) => void;
  onDismiss: () => void;
  pending?: boolean;
  // the list already warned about review once, so the card only carries a chip
  reviewNoted?: boolean;
}

function OperationsOnly({ suggestion }: { suggestion: RefreshSuggestionView }) {
  return (
    <ul className="flex flex-col gap-2">
      {suggestion.operations.map((operation) => (
        <li key={operation.op_id} className="rounded-sm border-l-2 border-success bg-[color-mix(in_oklab,var(--success)_8%,var(--card))] px-3 py-2">
          <p className="text-caption-1-medium text-success">{POSITION_LABELS[operation.position]}</p>
          <p className="text-body-regular text-foreground" dir="auto">
            <span aria-hidden className="mr-2 font-mono text-success">+</span>
            {operation.text}
          </p>
        </li>
      ))}
    </ul>
  );
}

export function RefreshCard({ suggestion, onApplied, onDismiss, pending = false, reviewNoted = false }: RefreshCardProps) {
  const rows = suggestion.preview_diff ? parseUnifiedDiff(suggestion.preview_diff, suggestion.operations) : null;
  const insertions = rows ? addedText(rows) : suggestion.operations.map((operation) => operation.text).join("\n");
  const keywords = [...new Set(suggestion.operations.flatMap((operation) => operation.keywords_added))];
  const usage = suggestion.resulting_bytes / DESCRIPTION_LIMIT_BYTES;
  // some rule-based fixes need details only the owner has (a booking link, a phone number), so there's nothing to paste
  const nothingToPaste = suggestion.operations.length === 0;

  if (suggestion.status !== "open") {
    return (
      <article className="flex flex-wrap items-center gap-3 rounded-card border border-dashed border-border-strong px-5 py-3.5">
        <span className="min-w-0 flex-1 truncate text-body-regular text-foreground">{suggestion.video.title ?? suggestion.video.video_id}</span>
        <Chip variant="caption" color={suggestion.status === "applied" ? "lime" : "soft"}>
          {suggestion.status === "applied" ? "Applied, we'll track the effect" : "Dismissed"}
        </Chip>
        {suggestion.applied_at ? <span className="text-caption-1-regular text-muted-foreground">{formatDate(suggestion.applied_at)}</span> : null}
      </article>
    );
  }

  return (
    <article id={suggestion.id} className="flex scroll-mt-24 flex-col gap-4 rounded-card bg-card p-4 shadow-card sm:p-5">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="mb-1.5 flex flex-wrap gap-1.5">
            <Chip variant="caption" color="gray">
              {suggestion.basis === "rule_based" ? "Hygiene fix" : "Refresh"}
            </Chip>
            {suggestion.review_required && reviewNoted ? (
              <Chip variant="caption" color="orange">
                Needs review
              </Chip>
            ) : null}
            {nothingToPaste ? null : suggestion.operations.some((operation) => operation.kind === "link_fix") ? (
              <Chip variant="caption" color="soft">
                Link fix
              </Chip>
            ) : (
              <Chip variant="caption" color="soft">
                Adds text only
              </Chip>
            )}
          </div>
          <h3 className="text-headline text-foreground" dir="auto">
            {suggestion.video.title ?? suggestion.video.video_id}
          </h3>
          {suggestion.video.published_at ? <p className="text-caption-1-regular text-muted-foreground">published {formatDate(suggestion.video.published_at)}</p> : null}
        </div>
        <a href={suggestion.video.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-body-medium text-info">
          Open on YouTube <RiExternalLinkLine aria-hidden className="size-4" />
        </a>
      </header>
      <p className="text-body-regular text-foreground">
        <span className="text-muted-foreground">Why now: </span>
        {suggestion.warranted_because.join("; ")}
      </p>
      {suggestion.advisories.map((advisory) => (
        <p key={advisory.kind} className="text-caption-1-regular text-muted-foreground">
          {advisory.message}
        </p>
      ))}
      {suggestion.review_required && !reviewNoted ? <InfoNote tone="warning">Needs medical review before you paste. Health wording must be checked by your team.</InfoNote> : null}
      {nothingToPaste ? (
        <InfoNote>There&apos;s no text to paste for this one: it needs details only you have. Fix it in YouTube Studio, then mark it applied.</InfoNote>
      ) : suggestion.description_changed ? (
        <InfoNote>Your description changed since we wrote this, so we won&apos;t show a diff against an old version. We&apos;ll re-check it tonight.</InfoNote>
      ) : rows ? (
        <DescriptionDiff rows={rows} />
      ) : (
        <OperationsOnly suggestion={suggestion} />
      )}
      {nothingToPaste ? null : (
        <div className="flex flex-col gap-1">
          <Meter
            aria-label="Description length"
            value={suggestion.resulting_bytes}
            maxValue={DESCRIPTION_LIMIT_BYTES}
            tone={usage > 0.9 ? "warning" : "success"}
            size="sm"
          />
          <p className="text-caption-1-regular text-muted-foreground tabular-nums">
            Adds {formatExact(suggestion.inserted_bytes)} bytes · {formatExact(suggestion.resulting_bytes - suggestion.inserted_bytes)} → {formatExact(suggestion.resulting_bytes)} of YouTube&apos;s{" "}
            {formatExact(DESCRIPTION_LIMIT_BYTES)}
            {keywords.length ? ` · keywords added: ${keywords.join(", ")}` : ""}
          </p>
        </div>
      )}
      <p className="text-body-regular text-muted-foreground">{suggestion.expected_effect}</p>
      <footer className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
        {suggestion.operations.length > 1 ? (
          <Button variant="secondary" size="xs" leadingIcon={RiFileCopyLine} onClick={() => copyWithToast(insertions, "Additions copied")}>
            Copy all additions
          </Button>
        ) : null}
        <Button variant="primary" size="xs" leadingIcon={RiCheckDoubleLine} disabled={pending || suggestion.description_changed} onClick={() => onApplied(insertions)}>
          Mark applied
        </Button>
        <Button variant="ghost" size="xs" leadingIcon={RiCloseLine} disabled={pending} onClick={onDismiss} className="sm:ml-auto">
          Not now
        </Button>
      </footer>
    </article>
  );
}
