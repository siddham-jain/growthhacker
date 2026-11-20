"use client";

import { LinkButton } from "@/components/ui/button";
import { Drawer, DrawerBody, DrawerHeader, TaskRows, type TaskRow } from "@/components/ui/primitives";
import type { PipelineRun, RunKind } from "@/lib/api/types";
import { formatRelative } from "@/lib/format";
import { isActiveRun } from "./run-status";

const KIND_LABELS: Record<RunKind, string> = {
  onboarding: "Setup",
  analysis: "Full analysis",
  daily_refresh: "Daily refresh",
  news_scan: "News scan",
  cards: "Writing cards",
  recrawl: "Re-reading website",
  backtest: "Backtest",
};

function toRow(run: PipelineRun): TaskRow {
  return {
    key: run.id,
    label: KIND_LABELS[run.kind],
    amount: run.started_at ? formatRelative(run.started_at) : "queued",
    status: isActiveRun(run) ? "running" : "done",
    step: Math.round(run.percent),
    details: [
      { label: "Status", meta: run.status.replace("_", " ") },
      { label: "Window", meta: `${run.window_days} days` },
      ...run.notices.map((notice) => ({ label: notice.message, meta: notice.code })),
    ],
  };
}

export default function ActivityDrawer({ clientId, runs, isOpen, onOpenChange }: { clientId: string; runs: PipelineRun[]; isOpen: boolean; onOpenChange: (open: boolean) => void }) {
  const active = runs.find(isActiveRun);
  return (
    <Drawer size="md" aria-label="Activity" isOpen={isOpen} onOpenChange={onOpenChange}>
      <DrawerHeader title="Activity" description="Recent runs for this client. Runs keep going if you leave." />
      <DrawerBody>
        <div className="flex flex-col gap-4">
          {active ? (
            <LinkButton href={`/c/${clientId}/setup/${active.kind === "onboarding" ? "analyze" : "analysis"}?run=${active.id}`} variant="primary">
              Watch the live analysis
            </LinkButton>
          ) : null}
          {runs.length ? <TaskRows variant="List" rows={runs.slice(0, 10).map(toRow)} /> : <p className="text-body-regular text-muted-foreground">No runs yet.</p>}
        </div>
      </DrawerBody>
    </Drawer>
  );
}
