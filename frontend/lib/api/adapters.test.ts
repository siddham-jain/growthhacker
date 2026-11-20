import { describe, expect, it } from "vitest";
import { subscriberBandLabel, toGapMatrix, toProfilePatch, toRecommendation, toRecommendationPage, toRun, toScorecard, toThinWindowNotice } from "./adapters";
import type { components } from "./schema";

type Wire = components["schemas"];

describe("adapters", () => {
  it("labels subscriber bands and passes unknown ones through", () => {
    expect(subscriberBandLabel("lt_10k")).toBe("Under 10K subscribers");
    expect(subscriberBandLabel("unknown_band")).toBe("unknown_band");
  });

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

  it("turns a thin-window notice into a sentence and the window to widen to", () => {
    const counts = (outliers: number, channels: number) => ({ outliers, channels, videos: 0, cards: 0 });
    const notice = { kind: "thin_window", reason_code: "THIN_WINDOW", window_days: 7, found: counts(2, 1), required: counts(3, 3), suggestion: { action: "widen_window", widen_to: 30 } } as Wire["InsufficiencyNotice"];
    const mapped = toThinWindowNotice(notice);
    expect(mapped).toEqual({ code: "thin_data", message: "Not enough outliers in the last 7 days (2 outliers found across 1 channel; we need 3+).", suggest: { widen_window_to: 30 } });
    expect(mapped.message).not.toMatch(/THIN_WINDOW/);
    expect(toThinWindowNotice({ ...notice, suggestion: { action: "add_competitors" } }).suggest).toBeNull();

    const page = toRecommendationPage({ data: { items: [], limit: 10, notices: [notice] }, notices: [] } as unknown as Wire["ResultEnvelope_RecommendationsData_"]);
    expect(page.notices).toEqual([mapped]);
  });

  it("maps the server gap matrix onto rows, columns and the client's own counts", () => {
    const cell = (n_videos: number, n_outliers: number, best_ratio: number | null) => ({ n_videos, n_outliers, best_ratio, latest_published_at: null });
    const matrix = toGapMatrix({
      rows: [{ topic_ref_id: "tr1", label: "Cost?", kind: "buyer_question" }],
      channels: [{ channel_id: "UC1", title: "Fortis", verified: false }],
      cells: { tr1: { UC1: cell(3, 1, 4) } },
      own_channel: { channel_id: "UC0", title: "Apollo", verified: true },
      own: { tr1: cell(0, 0, null) },
    } as Wire["GapMatrixData"]);
    expect(matrix.channels).toEqual([{ channel_id: "UC1", title: "Fortis" }]);
    expect(matrix.topics).toEqual([{ id: "tr1", label: "Cost?" }]);
    expect(matrix.cells.tr1.UC1).toEqual({ n_videos: 3, n_outliers: 1, best_ratio: 4 });
    expect(matrix.own.tr1).toBe(0);
  });

  it("writes profile edits as 03's merge patch keyed by item id", () => {
    const original = { id: "loc_1", name: "Navi Mumbai", kind: "hospital", city: "Navi Mumbai", meta: { status: "proposed" } };
    const lookup = (key: string) => (key === "locations:loc_1" ? original : undefined);
    const fact = { id: "loc_1", label: "Navi Mumbai", meta: { origin: "extracted" as const, status: "proposed" as const, confidence: 0.8, evidence: [] } };
    const patch = toProfilePatch(
      {
        facts: [
          { group: "offerings", op: "edit", id: "o1", label: "Cardiac sciences" },
          { group: "offerings", op: "remove", id: "o2" },
          { group: "conversion_actions", op: "add", id: "user_1", label: "+91 98765 43210" },
          { group: "value_propositions", op: "confirm", id: "vp1" },
          { group: "locations", op: "restore", fact },
        ],
        identity: { one_liner: "Hospitals across India" },
      },
      lookup,
    );
    expect(patch).toEqual({
      offerings: { o1: { name: "Cardiac sciences" }, o2: null },
      conversion_actions: { user_1: { label: "+91 98765 43210" } },
      value_propositions: { vp1: { status: "confirmed" } },
      locations: { loc_1: { name: "Navi Mumbai", kind: "hospital", city: "Navi Mumbai" } },
      identity: { one_liner: "Hospitals across India" },
    });
  });
});
