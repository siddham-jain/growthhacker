import type { GrowthHackerApi } from "../contract";
import type { RunEvent } from "../types";

// stands in for the fixture api in real builds so the demo data never ships to production bundles
function unavailable(): never {
  throw new Error("Mock mode is off. Set NEXT_PUBLIC_API_MOCK=1 to use the demo data.");
}

export const mockApi = new Proxy({}, { get: unavailable }) as GrowthHackerApi;

export const subscribeMockRun: (runId: string, afterSeq: number, onEvent: (event: RunEvent) => void) => () => void = unavailable;
