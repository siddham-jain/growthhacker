"use client";

import { CompetitorFolder } from "@/components/competitors/competitor-folder";
import type { LiveRunState } from "@/lib/runs/run-reducer";

const MAX_VISIBLE = 6;

export function CandidateStrip({ state }: { state: LiveRunState }) {
  if (!state.candidates.length) return null;
  const visible = state.candidates.slice(0, MAX_VISIBLE);
  const hidden = state.candidates.length - visible.length;
  return (
    <section aria-label="Competitors found" className="flex flex-col gap-3">
      <h3 className="text-eyebrow text-muted-foreground">Competitors found · {state.candidates.length}</h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {visible.map((candidate) => (
          <CompetitorFolder
            key={candidate.candidate_id}
            size="sm"
            name={candidate.name}
            previews={[]}
            count={candidate.evidence_count}
            countLabel="sources"
          />
        ))}
      </div>
      {hidden > 0 ? <p className="text-caption-1-regular text-muted-foreground">+{hidden} more</p> : null}
    </section>
  );
}
