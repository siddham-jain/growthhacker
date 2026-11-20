"use client";

import { RiArrowRightSLine, RiCheckLine, RiForbidLine } from "@remixicon/react";
import { SourceTrailLog, type SourceTrailStep } from "@/components/ui/primitives";
import { cx } from "@/lib/cx";
import { plural } from "@/lib/format";
import type { ContextSourceKind } from "@/lib/api/types";
import { SOURCE_KIND_LABELS } from "@/lib/labels";
import type { LiveRunState, SourceStepView } from "@/lib/runs/run-reducer";

const OUTCOME_ICON = { fell_back: RiForbidLine, used: RiCheckLine } as const;

// source names arrive as 03's source kinds; anything newer is shown as sent
export function sourceLabel(source: string): { label: string; description: string } {
  return SOURCE_KIND_LABELS[source as ContextSourceKind] ?? { label: source.replace(/_/g, " "), description: source };
}

function CascadeStep({ step }: { step: SourceStepView }) {
  const Icon = OUTCOME_ICON[step.outcome];
  return (
    <span
      className={cx(
        "flex animate-reveal items-center gap-1.5 rounded-full border px-2.5 py-1 text-caption-1-medium",
        step.outcome === "fell_back" && "border-danger/30 text-danger",
        step.outcome === "used" && "border-success/30 bg-[color-mix(in_oklab,var(--success)_8%,var(--card))] text-success",
      )}
      title={step.reason ?? sourceLabel(step.source).description}
    >
      <Icon aria-hidden className="size-3.5" />
      <span>{sourceLabel(step.source).label}</span>
      {step.outcome === "fell_back" && !step.pages.length ? <span>· blocked</span> : null}
      {step.pages.length ? <span className="tabular-nums">· {step.pages.length}</span> : null}
    </span>
  );
}

function toTrailStep(step: SourceStepView, index: number): SourceTrailStep {
  const label = sourceLabel(step.source).label;
  const blocked = step.outcome === "fell_back" && !step.pages.length;
  return {
    id: `${step.source}-${index}`,
    label: blocked ? `${label} blocked` : label,
    meta: blocked ? (step.reason ?? "blocked") : plural(step.pages.length, "page"),
    sources: step.pages.map((page) => ({ title: page.title ?? page.path, domain: page.path, href: page.url })),
  };
}

// shows the 03 §14 cascade: which sources refused us and which ones we routed through instead
export function SourceCascade({ state }: { state: LiveRunState }) {
  if (!state.sources.length) return null;
  const steps = state.sources.map(toTrailStep);
  const units = steps.reduce((sum, step) => sum + 1 + (step.sources?.length ? 1 : 0), 0);
  const blocked = state.sources.filter((step) => step.outcome === "fell_back" && !step.pages.length).length;
  return (
    <section aria-label="Where we read your business from" className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-eyebrow text-muted-foreground">Sources</h3>
        {blocked ? <span className="text-caption-1-regular text-muted-foreground">Routed around {blocked} blocked {blocked === 1 ? "source" : "sources"}</span> : null}
      </div>
      <ol className="flex flex-wrap items-center gap-1">
        {state.sources.map((step, index) => (
          <li key={`${step.source}-${index}`} className="flex items-center gap-1">
            {index > 0 ? <RiArrowRightSLine aria-hidden className="size-4 text-subtle-foreground" /> : null}
            <CascadeStep step={step} />
          </li>
        ))}
      </ol>
      <SourceTrailLog steps={steps} revealed={units} />
      {state.skippedPages ? <p className="text-caption-1-regular text-subtle-foreground">{plural(state.skippedPages, "page")} skipped (over budget or not allowed)</p> : null}
    </section>
  );
}
