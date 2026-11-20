"use client";

import { RiArrowRightSLine, RiCheckLine, RiForbidLine, RiLoader4Line } from "@remixicon/react";
import { SourceTrailLog, type SourceTrailStep } from "@/components/ui/primitives";
import { cx } from "@/lib/cx";
import { plural } from "@/lib/format";
import { SOURCE_KIND_LABELS } from "@/lib/labels";
import type { LiveRunState, SourceStepView } from "@/lib/runs/run-reducer";

const OUTCOME_ICON = { reading: RiLoader4Line, blocked: RiForbidLine, used: RiCheckLine } as const;

function CascadeStep({ step }: { step: SourceStepView }) {
  const Icon = OUTCOME_ICON[step.outcome];
  return (
    <span
      className={cx(
        "flex animate-reveal items-center gap-1.5 rounded-full border px-2.5 py-1 text-caption-1-medium",
        step.outcome === "blocked" && "border-danger/30 text-danger",
        step.outcome === "used" && "border-success/30 bg-[color-mix(in_oklab,var(--success)_8%,var(--card))] text-success",
        step.outcome === "reading" && "border-border text-muted-foreground",
      )}
      title={step.reason ?? SOURCE_KIND_LABELS[step.sourceKind].description}
    >
      <Icon aria-hidden className={cx("size-3.5", step.outcome === "reading" && "animate-spin motion-reduce:animate-none")} />
      <span>{SOURCE_KIND_LABELS[step.sourceKind].label}</span>
      {step.outcome === "blocked" ? <span>· blocked</span> : null}
      {step.pages.length ? <span className="tabular-nums">· {step.pages.length}</span> : null}
    </span>
  );
}

function toTrailStep(step: SourceStepView, index: number): SourceTrailStep {
  const label = SOURCE_KIND_LABELS[step.sourceKind].label;
  return {
    id: `${step.sourceKind}-${index}`,
    label: step.outcome === "blocked" ? `${label} blocked` : `${label}: ${step.target}`,
    meta: step.outcome === "blocked" ? (step.reason ?? "blocked") : plural(step.pages.length, "page"),
    sources: step.pages.map((page) => ({ title: page.title, domain: page.path, href: page.url })),
  };
}

// shows the 03 §14 cascade: which sources refused us and which ones we routed through instead
export function SourceCascade({ state }: { state: LiveRunState }) {
  if (!state.sources.length) return null;
  const steps = state.sources.map(toTrailStep);
  const units = steps.reduce((sum, step) => sum + 1 + (step.sources?.length ? 1 : 0), 0);
  const blocked = state.sources.filter((step) => step.outcome === "blocked").length;
  return (
    <section aria-label="Where we read your business from" className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-eyebrow text-muted-foreground">Sources</h3>
        {blocked ? <span className="text-caption-1-regular text-muted-foreground">Routed around {blocked} blocked {blocked === 1 ? "source" : "sources"}</span> : null}
      </div>
      <ol className="flex flex-wrap items-center gap-1">
        {state.sources.map((step, index) => (
          <li key={`${step.sourceKind}-${index}`} className="flex items-center gap-1">
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
