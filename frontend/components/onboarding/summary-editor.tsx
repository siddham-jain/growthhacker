"use client";

import { RiPencilLine } from "@remixicon/react";
import { useState } from "react";
import { InfoNote } from "@/components/states/states";
import type { BusinessProfileView } from "@/lib/api/types";
import { countryOptions, marketLine } from "@/lib/onboarding/market";
import { SourcesUsed } from "./sources-used";

interface SummaryEditorProps {
  view: BusinessProfileView;
  onSummary: (text: string) => void;
  onCountry: (code: string) => void;
}

export function SummaryEditor({ view, onSummary, onCountry }: SummaryEditorProps) {
  const identity = view.effective.identity;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(identity.one_liner);
  const [pickingCountry, setPickingCountry] = useState(false);

  function commit() {
    const text = draft.replace(/\s+/g, " ").trim();
    setEditing(false);
    if (text && text !== identity.one_liner) onSummary(text);
    else setDraft(identity.one_liner);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-card bg-card p-5 shadow-card">
        {editing ? (
          <textarea
            aria-label="Correct your one-line summary"
            value={draft}
            autoFocus
            rows={3}
            maxLength={300}
            onFocus={(event) => event.currentTarget.select()}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={commit}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                commit();
              } else if (event.key === "Escape") {
                event.preventDefault();
                setDraft(identity.one_liner);
                setEditing(false);
              }
            }}
            className="w-full resize-none rounded-control border border-ring bg-background p-2 text-[1.25rem] leading-8 text-foreground outline-none"
          />
        ) : (
          <button
            type="button"
            onClick={() => {
              setDraft(identity.one_liner);
              setEditing(true);
            }}
            title="Click to correct"
            className="group flex w-full cursor-text items-start gap-3 text-left text-[1.25rem] leading-8 text-foreground"
          >
            <span className="flex-1 decoration-border-strong underline-offset-[6px] group-hover:underline">{identity.one_liner || "Add a one-line summary of what you do"}</span>
            <RiPencilLine aria-hidden className="mt-2 size-4 shrink-0 text-subtle-foreground group-hover:text-foreground" />
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-eyebrow text-muted-foreground">Market</span>
        {pickingCountry ? (
          <select
            aria-label="Your main market"
            autoFocus
            defaultValue={identity.country}
            onBlur={() => setPickingCountry(false)}
            onChange={(event) => {
              setPickingCountry(false);
              if (event.target.value && event.target.value !== identity.country) onCountry(event.target.value);
            }}
            className="h-9 rounded-control border border-border bg-card px-2 text-body-regular text-foreground"
          >
            {identity.country ? null : <option value="">Pick a country</option>}
            {countryOptions(identity.country).map((option) => (
              <option key={option.code} value={option.code}>
                {option.name}
              </option>
            ))}
          </select>
        ) : (
          <button type="button" onClick={() => setPickingCountry(true)} className="group inline-flex items-center gap-2 text-body-regular text-foreground" title="Change your market">
            <span className="decoration-border-strong underline-offset-4 group-hover:underline">{marketLine(identity.country, view.effective.locations)}</span>
            <RiPencilLine aria-hidden className="size-3.5 text-subtle-foreground group-hover:text-foreground" />
          </button>
        )}
      </div>

      {view.partial || view.completeness < 0.6 ? (
        <InfoNote tone="warning">Your site limits automated reading, so we filled gaps from other public sources. We&apos;ll flag those facts with “Check this”.</InfoNote>
      ) : null}
      <details className="group">
        <summary className="cursor-pointer list-none text-caption-1-regular text-muted-foreground hover:text-foreground">
          Where we read this · <span className="font-mono">{view.sources.length}</span> {view.sources.length === 1 ? "source" : "sources"}
        </summary>
        <div className="pt-3">
          <SourcesUsed sources={view.sources} />
        </div>
      </details>
    </div>
  );
}
