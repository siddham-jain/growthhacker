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
  resolve_inputs: RiYoutubeLine,
  crawl_site: RiGlobalLine,
  prefetch_own_channel: RiYoutubeLine,
  build_profile: RiBookOpenLine,
  discover_competitors: RiFolder3Line,
  seed_topic_refs: RiSearchLine,
  prefetch_competitors: RiYoutubeLine,
  ingest: RiStackLine,
  niche_discovery: RiFolder3Line,
  news: RiRadarLine,
  enrich: RiSearchLine,
  topics: RiSearchLine,
  analytics: RiPulseLine,
  recommend: RiLightbulbFlashLine,
  write_cards: RiLightbulbFlashLine,
  refresh: RiLoopLeftLine,
};

const VISIBLE_STEPS = 5;
const FINISHED = new Set(["succeeded", "degraded", "reused", "skipped", "failed"]);

// task-list reveals by unit count (one per header, one per step); a running stage keeps one hidden
// placeholder step so the list doesn't mark it complete before the server does. stages run as a graph,
// so finished ones come first and only the first running one shows; ones not started yet are left out
export function StageRail({ state }: { state: LiveRunState }) {
  const seen = state.stageOrder.map((id) => state.stages[id]).filter((stage) => stage && stage.status !== "pending");
  const finished = seen.filter((stage) => FINISHED.has(stage.status));
  const running = seen.find((stage) => !FINISHED.has(stage.status));
  let revealed = 0;
  const tasks: StageTask[] = [];
  for (const stage of running ? [...finished, running] : finished) {
    const steps = stage.steps.slice(-VISIBLE_STEPS).map((label) => ({ label }));
    const done = stage !== running;
    tasks.push({ id: stage.id, label: stage.label, completedLabel: stage.message ?? stage.label, icon: STAGE_ICONS[stage.id], steps: done ? steps : [...steps, { label: "" }] });
    revealed += 1 + steps.length;
  }
  if (!tasks.length) return <p className="text-body-regular text-muted-foreground">Starting soon…</p>;
  return <TaskList tasks={tasks} revealed={revealed} collapseOnComplete />;
}
