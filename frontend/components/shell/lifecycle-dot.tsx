import { StatusDot } from "@/components/ui/primitives";
import type { ClientLifecycle } from "@/lib/api/types";

const COLOR: Record<ClientLifecycle, "green" | "yellow" | "indigo"> = {
  active: "green",
  paused: "yellow",
  created: "indigo",
  onboarding_failed: "yellow",
  archived: "yellow",
  deleting: "yellow",
  onboarding: "indigo",
  analyzing: "indigo",
  awaiting_confirmation: "yellow",
};

const LABEL: Record<ClientLifecycle, string> = {
  active: "Active",
  paused: "Paused",
  created: "Setting up",
  onboarding_failed: "Needs attention",
  archived: "Archived",
  deleting: "Deleting",
  onboarding: "Setting up",
  analyzing: "Analysing",
  awaiting_confirmation: "Needs confirmation",
};

export function LifecycleDot({ lifecycle }: { lifecycle: ClientLifecycle }) {
  return (
    <span className="inline-flex" title={LABEL[lifecycle]}>
      <StatusDot color={COLOR[lifecycle]} />
      <span className="sr-only">{LABEL[lifecycle]}</span>
    </span>
  );
}

export function lifecycleLabel(lifecycle: ClientLifecycle): string {
  return LABEL[lifecycle];
}
