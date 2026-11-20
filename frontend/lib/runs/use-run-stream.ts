"use client";

import { useQuery } from "@tanstack/react-query";
import { usePrefersReducedMotion } from "@/hooks/use-media-state";
import { useEffect, useReducer, useRef } from "react";
import { runQuery } from "@/lib/api/queries";
import type { RunEvent } from "@/lib/api/types";
import { initialRunState, runReducer } from "./run-reducer";
import { openRunStream } from "./stream";

const REPLAY_AGE_MS = 10_000;

// slower with a short backlog, faster as it grows, never far behind real time
function dwellFor(backlog: number): number {
  return Math.min(600, Math.max(120, 900 / Math.max(1, backlog / 4)));
}

function isPaced(event: RunEvent): boolean {
  return event.type === "insight" || event.type === "notice";
}

export function useRunStream(runId: string | null) {
  const [state, dispatch] = useReducer(runReducer, runId ?? "", initialRunState);
  const reduceMotion = usePrefersReducedMotion();
  const pending = useRef<RunEvent[]>([]);
  const ticker = useRef<ReturnType<typeof setTimeout> | null>(null);
  const run = useQuery({ ...runQuery(runId ?? ""), enabled: Boolean(runId) });

  useEffect(() => {
    if (run.data) dispatch({ type: "seed", run: run.data });
  }, [run.data]);

  useEffect(() => {
    if (!runId) return;
    const queue = pending.current;

    const release = () => {
      const next = queue.shift();
      if (!next) {
        ticker.current = null;
        return;
      }
      dispatch({ type: "event", event: next });
      ticker.current = setTimeout(release, dwellFor(queue.length));
    };

    const flush = () => {
      if (ticker.current) clearTimeout(ticker.current);
      ticker.current = null;
      queue.splice(0).forEach((event) => dispatch({ type: "event", event }));
    };

    const onEvent = (event: RunEvent) => {
      const replayed = Date.now() - Date.parse(event.ts) > REPLAY_AGE_MS;
      if (replayed || reduceMotion || !isPaced(event)) {
        flush();
        dispatch({ type: "event", event });
        return;
      }
      queue.push(event);
      if (!ticker.current) release();
    };

    const close = openRunStream(runId, 0, {
      onEvent,
      onConnection: (connection) => dispatch({ type: "connection", state: connection }),
    });
    return () => {
      close();
      flush();
    };
  }, [runId, reduceMotion]);

  return { state, run: run.data ?? null, error: run.error };
}
