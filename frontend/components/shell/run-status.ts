import type { PipelineRun } from "@/lib/api/types";

export function isActiveRun(run: PipelineRun): boolean {
  return run.status === "queued" || run.status === "running";
}
