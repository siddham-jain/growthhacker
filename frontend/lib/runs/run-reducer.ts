import type { EventPayload, PipelineRun, RunEvent, RunKind, RunStatus, StageStatus } from "@/lib/api/types";
import type { ConnectionState } from "./stream";
import { RUN_STAGES, STAGE_LABELS } from "./stages";

type PayloadOf<K extends EventPayload["kind"]> = Extract<EventPayload, { kind: K }>;

export interface StageView {
  id: string;
  label: string;
  status: StageStatus;
  message: string | null;
  steps: string[];
}

// the 03 §14 cascade as the backend reports it: each source_fallback closes one source and opens the next
export interface SourceStepView {
  source: string;
  outcome: "used" | "fell_back";
  reason: string | null;
  pages: PayloadOf<"page_read">[];
}

export interface InsightView {
  seq: number;
  payload: PayloadOf<"outlier_found" | "trait_lift" | "news_found" | "card_written" | "refresh_suggested" | "videos_fetched">;
}

export interface NoticeView {
  seq: number;
  level: RunEvent["level"];
  message: string;
}

export interface LiveRunState {
  runId: string;
  kind: RunKind | null;
  status: RunStatus;
  percent: number;
  lastSeq: number;
  startedAt: string | null;
  latestMessage: string | null;
  stageOrder: string[];
  stages: Record<string, StageView>;
  sources: SourceStepView[];
  skippedPages: number;
  facts: PayloadOf<"profile_field">[];
  channel: PayloadOf<"channel_resolved"> | null;
  candidates: PayloadOf<"competitor_candidate">[];
  insights: InsightView[];
  notices: NoticeView[];
  connection: ConnectionState;
}

export type RunAction =
  | { type: "seed"; run: PipelineRun }
  | { type: "event"; event: RunEvent }
  | { type: "connection"; state: ConnectionState };

export function initialRunState(runId: string): LiveRunState {
  return {
    runId,
    kind: null,
    status: "queued",
    percent: 0,
    lastSeq: 0,
    startedAt: null,
    latestMessage: null,
    stageOrder: [],
    stages: {},
    sources: [],
    skippedPages: 0,
    facts: [],
    channel: null,
    candidates: [],
    insights: [],
    notices: [],
    connection: "connecting",
  };
}

// known stages show as pending up front; stages already seen from events keep their state
function seedStages(state: LiveRunState, kind: RunKind): Pick<LiveRunState, "stageOrder" | "stages"> {
  const known = RUN_STAGES[kind] ?? [];
  const defaults = Object.fromEntries(
    known.map((id) => [id, { id, label: STAGE_LABELS[id] ?? id, status: "pending" as StageStatus, message: null, steps: [] }]),
  );
  return {
    stageOrder: [...known, ...state.stageOrder.filter((id) => !known.includes(id))],
    stages: { ...defaults, ...state.stages },
  };
}

function applyStage(state: LiveRunState, event: RunEvent): LiveRunState {
  const id = event.stage;
  if (!id) return state;
  const existing = state.stages[id] ?? { id, label: STAGE_LABELS[id] ?? event.message, status: "pending" as StageStatus, message: null, steps: [] };
  const status = event.type === "stage.progress" || event.type === "stage.step" ? existing.status : (event.status as StageStatus | null) ?? existing.status;
  const next: StageView = {
    ...existing,
    status: event.type === "stage.started" ? "running" : status,
    message: event.type === "stage.started" ? existing.message : event.message,
  };
  return {
    ...state,
    stageOrder: state.stageOrder.includes(id) ? state.stageOrder : [...state.stageOrder, id],
    stages: { ...state.stages, [id]: next },
  };
}

function addStep(state: LiveRunState, event: RunEvent): LiveRunState {
  const id = event.stage;
  const stage = id ? state.stages[id] : undefined;
  if (!id || !stage) return state;
  return { ...state, stages: { ...state.stages, [id]: { ...stage, steps: [...stage.steps, event.message] } } };
}

function withSource(state: LiveRunState, source: string): LiveRunState {
  return state.sources.length ? state : { ...state, sources: [{ source, outcome: "used", reason: null, pages: [] }] };
}

function applyPayload(state: LiveRunState, event: RunEvent, payload: EventPayload): LiveRunState {
  switch (payload.kind) {
    case "source_fallback": {
      const sources = withSource(state, payload.from_source).sources.map((step, index, all) =>
        index === all.length - 1 ? { ...step, outcome: "fell_back" as const, reason: payload.reason ?? null } : step,
      );
      return { ...state, sources: [...sources, { source: payload.to_source, outcome: "used", reason: null, pages: [] }] };
    }
    case "page_read": {
      const sources = [...withSource(state, "site_direct").sources];
      const last = sources[sources.length - 1];
      sources[sources.length - 1] = { ...last, pages: [...last.pages, payload] };
      return { ...state, sources };
    }
    case "page_skipped":
      return { ...state, skippedPages: state.skippedPages + 1 };
    case "profile_field":
      return { ...state, facts: [...state.facts.filter((fact) => fact.field !== payload.field), payload] };
    case "channel_resolved":
      return { ...state, channel: payload };
    case "competitor_candidate":
      return { ...state, candidates: [...state.candidates, payload] };
    case "outlier_found":
    case "trait_lift":
    case "news_found":
    case "card_written":
    case "refresh_suggested":
    case "videos_fetched":
      return { ...state, insights: [...state.insights, { seq: event.seq, payload }] };
    default:
      // unknown or display-less payload kinds are ignored by contract
      return state;
  }
}

function applyEvent(state: LiveRunState, event: RunEvent): LiveRunState {
  if (event.seq <= state.lastSeq) return state;
  let next: LiveRunState = {
    ...state,
    lastSeq: event.seq,
    percent: Math.max(state.percent, event.percent ?? 0),
    latestMessage: event.message || state.latestMessage,
  };

  if (event.type.startsWith("run.") && event.status) {
    next = { ...next, status: event.status as RunStatus };
    if (event.type === "run.started") next = { ...next, startedAt: next.startedAt ?? event.ts };
  } else if (event.type.startsWith("stage.")) {
    next = event.type === "stage.step" ? addStep(next, event) : applyStage(next, event);
  }

  if ((event.type === "notice" || event.level !== "info") && !event.payload) {
    next = { ...next, notices: [...next.notices, { seq: event.seq, level: event.level, message: event.message }] };
  }
  if (event.payload) {
    next = addStep(applyPayload(next, event, event.payload), event);
  }
  return next;
}

export function runReducer(state: LiveRunState, action: RunAction): LiveRunState {
  switch (action.type) {
    case "seed": {
      return {
        ...state,
        ...seedStages(state, action.run.kind),
        kind: action.run.kind,
        status: state.lastSeq ? state.status : action.run.status,
        percent: Math.max(state.percent, action.run.percent),
        startedAt: state.startedAt ?? action.run.started_at,
      };
    }
    case "event":
      return applyEvent(state, action.event);
    case "connection":
      return { ...state, connection: action.state };
  }
}
