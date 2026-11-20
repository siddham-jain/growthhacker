import type { PipelineRun, RunEvent, RunKind, RunStatus, WindowDays } from "../types";
import { ApiError } from "../errors";
import { CLIENT_ID } from "./fixtures/base";
import { RUN_SCRIPTS, type ScriptedEvent } from "./run-scripts";

interface MockRun {
  id: string;
  clientId: string;
  kind: RunKind;
  windowDays: WindowDays;
  startedAt: number;
}

const runs = new Map<string, MockRun>();

function scriptFor(kind: RunKind): ScriptedEvent[] {
  return RUN_SCRIPTS[kind] ?? RUN_SCRIPTS.analysis ?? [];
}

function timeline(run: MockRun): { at: number; event: RunEvent }[] {
  let at = run.startedAt;
  return scriptFor(run.kind).map((scripted, index) => {
    at += scripted.delayMs;
    return {
      at,
      event: {
        run_id: run.id,
        client_id: run.clientId,
        seq: index + 1,
        type: scripted.type,
        stage: scripted.stage ?? null,
        step: scripted.payload ? `${scripted.stage}.${scripted.payload.kind}` : null,
        status: scripted.status ?? null,
        level: scripted.level ?? "info",
        message: scripted.message,
        counts: scripted.counts ?? null,
        percent: scripted.percent ?? null,
        stage_percent: null,
        ts: new Date(at).toISOString(),
        payload: scripted.payload ?? null,
      },
    };
  });
}

export function startMockRun(clientId: string, kind: RunKind, windowDays: WindowDays, startedAt = Date.now()): PipelineRun {
  const id = `run_${kind}_${startedAt.toString(36)}`;
  runs.set(id, { id, clientId, kind, windowDays, startedAt });
  return getMockRun(id);
}

// run ids encode kind and start time, so a page reload can rebuild the run and replay its history
function requireRun(runId: string): MockRun {
  const existing = runs.get(runId);
  if (existing) return existing;
  const match = /^run_([a-z_]+)_([0-9a-z]+)$/.exec(runId);
  if (!match) throw new ApiError(404, "not_found", "We couldn't find that run.");
  const run: MockRun = { id: runId, clientId: CLIENT_ID, kind: match[1] as RunKind, windowDays: 30, startedAt: parseInt(match[2], 36) };
  runs.set(runId, run);
  return run;
}

export function getMockRun(runId: string): PipelineRun {
  const run = requireRun(runId);
  const entries = timeline(run);
  const delivered = entries.filter((entry) => entry.at <= Date.now());
  const terminal = [...delivered].reverse().find((entry) => entry.event.type.startsWith("run.") && entry.event.status);
  const percent = Math.max(0, ...delivered.map((entry) => entry.event.percent ?? 0));
  const finished = delivered.length === entries.length;
  return {
    id: run.id,
    client_id: run.clientId,
    kind: run.kind,
    status: (terminal?.event.status as RunStatus | undefined) ?? "queued",
    percent,
    window_days: run.windowDays,
    started_at: new Date(run.startedAt).toISOString(),
    finished_at: finished ? new Date(entries[entries.length - 1].at).toISOString() : null,
    notices: [],
    error: null,
  };
}

export function listMockRuns(clientId: string): PipelineRun[] {
  return [...runs.values()]
    .filter((run) => run.clientId === clientId)
    .sort((a, b) => b.startedAt - a.startedAt)
    .map((run) => getMockRun(run.id));
}

export function mockEventsAfter(runId: string, afterSeq: number): RunEvent[] {
  return timeline(requireRun(runId))
    .filter((entry) => entry.at <= Date.now() && entry.event.seq > afterSeq)
    .map((entry) => entry.event);
}

// replays already-due events at once, then emits the rest on their scripted schedule
export function subscribeMockRun(runId: string, afterSeq: number, onEvent: (event: RunEvent) => void): () => void {
  const timers = timeline(requireRun(runId))
    .filter((entry) => entry.event.seq > afterSeq)
    .map((entry) => setTimeout(() => onEvent(entry.event), Math.max(0, entry.at - Date.now())));
  return () => timers.forEach(clearTimeout);
}
