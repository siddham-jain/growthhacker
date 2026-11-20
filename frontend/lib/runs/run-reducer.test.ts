import { describe, expect, it } from "vitest";
import type { RunEvent } from "@/lib/api/types";
import { initialRunState, runReducer } from "./run-reducer";

function event(seq: number, overrides: Partial<RunEvent> = {}): RunEvent {
  return { run_id: "run_1", client_id: "cl", seq, type: "run.progress", stage: null, step: null, status: null, level: "info", message: "", counts: null, percent: null, stage_percent: null, ts: new Date().toISOString(), payload: null, ...overrides };
}

describe("runReducer", () => {
  it("drops replayed events and keeps percent monotonic", () => {
    let state = initialRunState("run_1");
    state = runReducer(state, { type: "event", event: event(1, { percent: 40 }) });
    state = runReducer(state, { type: "event", event: event(2, { percent: 30 }) });
    const replay = runReducer(state, { type: "event", event: event(1, { percent: 90 }) });
    expect(replay).toBe(state);
    expect(state.percent).toBe(40);
    expect(state.lastSeq).toBe(2);
  });

  it("ignores payload kinds it doesn't know", () => {
    const state = runReducer(initialRunState("run_1"), { type: "event", event: event(1, { type: "insight", payload: { kind: "future_kind" } as unknown as RunEvent["payload"] }) });
    expect(state.lastSeq).toBe(1);
    expect(state.insights).toHaveLength(0);
  });
});
