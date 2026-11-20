"use client";

import {
  RiBookOpenLine,
  RiFolder3Line,
  RiGlobalLine,
  RiLightbulbFlashLine,
  RiLoopLeftLine,
  RiPulseLine,
  RiRadarLine,
  RiSearchLine,
  RiStackLine,
  RiYoutubeLine,
} from "@remixicon/react";
import { TaskList, type StageTask } from "@/components/ui/primitives";
import type { LiveRunState } from "@/lib/runs/run-reducer";

const STAGE_ICONS: Record<string, StageTask["icon"]> = {
  crawl: RiGlobalLine,
  profile: RiBookOpenLine,
  resolve: RiYoutubeLine,
  discover: RiFolder3Line,
  ingest: RiStackLine,
  metrics: RiPulseLine,
  patterns: RiSearchLine,
  news: RiRadarLine,
  write: RiLightbulbFlashLine,
  refresh: RiLoopLeftLine,
};

const VISIBLE_STEPS = 5;
const FINISHED = new Set(["succeeded", "degraded", "reused", "skipped", "failed"]);

// task-list reveals by unit count (one per header, one per step); a running stage keeps one hidden
// placeholder step so the list doesn't mark it complete before the server does
export function StageRail({ state }: { state: LiveRunState }) {
  let revealed = 0;
  const tasks: StageTask[] = [];
  for (const id of state.stageOrder) {
    const stage = state.stages[id];
    if (!stage || stage.status === "pending") break;
    const steps = stage.steps.slice(-VISIBLE_STEPS).map((label) => ({ label }));
    const finished = FINISHED.has(stage.status);
    tasks.push({
      id,
      label: stage.label,
      completedLabel: stage.message ?? stage.label,
      icon: STAGE_ICONS[id],
      steps: finished ? steps : [...steps, { label: "" }],
    });
    revealed += 1 + steps.length;
    if (!finished) break;
  }
  if (!tasks.length) return <p className="text-body-regular text-muted-foreground">Starting soon…</p>;
  return <TaskList tasks={tasks} revealed={revealed} collapseOnComplete />;
}
