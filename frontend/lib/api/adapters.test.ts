import { describe, expect, it } from "vitest";
import { toGapMatrix, toProfilePatch, toRecommendation, toRun, toScorecard } from "./adapters";
import type { components } from "./schema";

type Wire = components["schemas"];

describe("adapters", () => {
  it("renames run progress and keeps the window", () => {
    const run = toRun({ id: "r", client_id: "c", kind: "analysis", status: "running", progress_pct: 42, window_days: 90, notices: [] } as unknown as Wire["RunOut"]);
    expect(run).toMatchObject({ percent: 42, window_days: 90, notices: [], error: null });
  });

  it("flattens a recommendation and its feedback", () => {
    const rec = toRecommendation({
      recommendation: { id: "rec", window_days: 30, reference_videos: [], extras: { stable_key: "k", run_id: "run", rendered_by: "template" } },
      feedback: { status: "saved", reason: null, linked_video_id: null },
    } as unknown as Wire["RecommendationOut"]);
    expect(rec).toMatchObject({ id: "rec", stable_key: "k", rendered_by: "template", feedback: { status: "saved", reason: null, linked_video_id: null } });
  });

  it("states the wilson interval as counts of the same denominator", () => {
    const sheet = toScorecard(
      { channel_id: "UC1", title: "Fortis", verified: true },
      { role: "competitor", outlier_hit_rate: [3, 12], outlier_hit_rate_wilson: [0.09, 0.53], median_views_at_age: { long_form: { value: 8600, method: "observed", n: 12 } }, segment_mix: {}, subscriber_band: "hidden", uploads_in_window: 12, uploads_per_week: 2.9, median_ratio: 1.3, days_since_last_upload: 2.4, longest_gap_days: 9.2, topic_coverage: {} } as unknown as Wire["ChannelScorecard"],
      [],
    );
    expect(sheet.outlier_hit_rate).toEqual({ outliers: 3, videos: 12, likely_low: 1, likely_high: 6 });
    expect(sheet).toMatchObject({ role: "competitor_set", subscriber_hidden: true, median_views_at_age: { views: 8600, method: "observed" }, relation: null });
  });

  it("assembles the gap matrix from each sheet's topic coverage, confirmed questions only", () => {
    const sheets = [
      { channel: { channel_id: "UC1", title: "Fortis", verified: true }, scorecard: { role: "competitor", topic_coverage: { q1: { n_videos: 3, n_outliers: 1, best_ratio: 4 } } } },
      { channel: { channel_id: "UC2", title: "Practo", verified: true }, scorecard: { role: "niche", topic_coverage: {} } },
    ] as unknown as Wire["CompetitorSheet"][];
    const questions = [
      { id: "q1", text: "Cost?", status: "confirmed" },
      { id: "q2", text: "Recovery?", status: "proposed" },
    ] as unknown as Wire["BuyerQuestionItem"][];
    const matrix = toGapMatrix(sheets, questions, [{ topic_ref: { id: "q1" }, own_videos: 0 } as unknown as Wire["TopicSummary"]]);
    expect(matrix.channels.map((channel) => channel.channel_id)).toEqual(["UC1"]);
    expect(matrix.topics.map((topic) => topic.id)).toEqual(["q1"]);
    expect(matrix.cells.q1.UC1).toEqual({ n_videos: 3, n_outliers: 1, best_ratio: 4 });
    expect(matrix.own.q1).toBe(0);
  });

  it("writes profile edits back onto the domain objects without dropping their other fields", () => {
    const raw = { version: 4, offerings: [{ id: "o1", name: "Cardiology", kind: "department", meta: { origin: "extracted", status: "proposed", confidence: 0.8, first_seen_version: 1, updated_at: "x" } }] } as unknown as Wire["BusinessProfile"];
    const patch = toProfilePatch(raw, { offerings: [{ id: "o1", label: "Cardiac sciences", meta: { origin: "user", status: "edited", confidence: 1, evidence: [] } }] });
    expect(patch.offerings).toMatchObject([{ id: "o1", name: "Cardiac sciences", kind: "department", meta: { status: "edited", origin: "user", first_seen_version: 1 } }]);
  });
});
