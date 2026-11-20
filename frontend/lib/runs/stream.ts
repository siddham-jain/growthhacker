import { api } from "@/lib/api";
import { apiPath, buildUrl } from "@/lib/api/http";
import { subscribeMockRun } from "@/lib/api/mock/runs";
import type { RunEvent, RunEventType } from "@/lib/api/types";
import { API_MOCK } from "@/lib/env";

export type ConnectionState = "connecting" | "live" | "reconnecting" | "polling" | "closed";

export interface RunStreamHandlers {
  onEvent: (event: RunEvent) => void;
  onConnection: (state: ConnectionState) => void;
}

const TERMINAL_TYPES = new Set<RunEventType>([
  "run.succeeded",
  "run.partial",
  "run.failed",
  "run.cancelled",
  "run.superseded",
  "run.awaiting_confirmation",
]);

const EVENT_TYPES: RunEventType[] = [
  "run.queued", "run.started", "run.progress", "run.awaiting_confirmation", "run.succeeded", "run.partial",
  "run.failed", "run.cancelled", "run.superseded", "stage.started", "stage.step", "stage.progress",
  "stage.retrying", "stage.reused", "stage.succeeded", "stage.degraded", "stage.failed", "stage.skipped",
  "insight", "notice", "results.published",
];

const MAX_SSE_FAILURES = 3;
const POLL_MS = 3_000;
const SSE_RETRY_WHILE_POLLING_MS = 60_000;

export function isTerminal(event: RunEvent): boolean {
  return TERMINAL_TYPES.has(event.type);
}

export function openRunStream(runId: string, afterSeq: number, handlers: RunStreamHandlers): () => void {
  if (API_MOCK) {
    handlers.onConnection("live");
    return subscribeMockRun(runId, afterSeq, (event) => {
      handlers.onEvent(event);
      if (isTerminal(event)) handlers.onConnection("closed");
    });
  }
  return openServerStream(runId, afterSeq, handlers);
}

function openServerStream(runId: string, afterSeq: number, handlers: RunStreamHandlers): () => void {
  let lastSeq = afterSeq;
  let failures = 0;
  let closed = false;
  let polling = false;
  let source: EventSource | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const setConnection = (state: ConnectionState) => {
    if (!closed) handlers.onConnection(state);
  };

  const stopPolling = () => {
    polling = false;
    if (pollTimer) clearTimeout(pollTimer);
  };

  const finish = () => {
    if (closed) return;
    closed = true;
    source?.close();
    stopPolling();
    if (retryTimer) clearTimeout(retryTimer);
    handlers.onConnection("closed");
  };

  const deliver = (event: RunEvent) => {
    if (event.seq <= lastSeq) return;
    lastSeq = event.seq;
    handlers.onEvent(event);
    if (isTerminal(event)) finish();
  };

  // a live event that skips seq numbers means we missed some; backfill them before applying it
  const receive = async (event: RunEvent) => {
    if (event.seq > lastSeq + 1) {
      const gap = await api.runs.events(runId, lastSeq, event.seq - lastSeq - 1);
      gap.items.forEach(deliver);
    }
    deliver(event);
  };

  const poll = async () => {
    if (!polling || closed) return;
    setConnection("polling");
    try {
      const page = await api.runs.events(runId, lastSeq);
      page.items.forEach(deliver);
    } catch {
      // a failed poll is retried on the next tick
    }
    if (polling && !closed) pollTimer = setTimeout(poll, POLL_MS);
  };

  const connect = () => {
    if (closed) return;
    if (!polling) setConnection(failures === 0 ? "connecting" : "reconnecting");
    // the browser resends Last-Event-ID on its own reconnects; after_seq covers streams we reopen ourselves
    source = new EventSource(buildUrl(apiPath`/runs/${runId}/events`, { after_seq: lastSeq }), { withCredentials: true });
    const onMessage = (message: MessageEvent<string>) => void receive(JSON.parse(message.data) as RunEvent);
    source.onopen = () => {
      failures = 0;
      stopPolling();
      setConnection("live");
    };
    source.onmessage = onMessage;
    EVENT_TYPES.forEach((type) => source?.addEventListener(type, onMessage as EventListener));
    source.addEventListener("end", finish);
    source.onerror = () => {
      if (source?.readyState === EventSource.CONNECTING) {
        if (!polling) setConnection("reconnecting");
        return;
      }
      source?.close();
      failures += 1;
      if (failures >= MAX_SSE_FAILURES) {
        if (!polling) {
          polling = true;
          void poll();
        }
        retryTimer = setTimeout(connect, SSE_RETRY_WHILE_POLLING_MS);
        return;
      }
      retryTimer = setTimeout(connect, Math.min(30_000, 1_000 * 2 ** failures) + Math.random() * 500);
    };
  };

  connect();
  return finish;
}
