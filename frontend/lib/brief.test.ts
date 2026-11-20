import { describe, expect, it } from "vitest";
import { newsOpportunities, recommendations, refreshSuggestions } from "@/lib/api/mock/fixtures/insights";
import type { RecommendationOut } from "@/lib/api/types";
import { BRIEF_CONFIG, composeBrief } from "./brief";

const base = { clientId: "cl_test", news: newsOpportunities, refresh: refreshSuggestions, setupGap: null, thinNotice: null, now: Date.now() };

describe("composeBrief", () => {
  it("leads with urgent news, then video ideas, then a refresh, capped at five", () => {
    const items = composeBrief({ ...base, recommendations });
    expect(items).toHaveLength(BRIEF_CONFIG.maxItems);
    expect(items.map((item) => item.kind)).toEqual(["news", "video_idea", "video_idea", "refresh", "pattern"]);
  });

  it("only selects existing objects and keeps their ids", () => {
    const ids = new Set([...recommendations.map((rec) => rec.id), ...newsOpportunities.map((item) => item.id), ...refreshSuggestions.map((item) => item.id)]);
    composeBrief({ ...base, recommendations }).forEach((item) => expect(ids.has(item.id)).toBe(true));
  });

  it("puts a setup gap first", () => {
    const items = composeBrief({ ...base, recommendations, setupGap: "Confirm at least 3 competitors" });
    expect(items[0]).toMatchObject({ kind: "setup", href: "/c/cl_test/settings?tab=competitors" });
  });

  it("skips dismissed and done ideas", () => {
    const dismissed: RecommendationOut[] = recommendations.map((rec, index) => (index < 2 ? { ...rec, feedback: { status: "dismissed" } } : rec));
    const items = composeBrief({ ...base, recommendations: dismissed });
    expect(items.some((item) => item.id === recommendations[0].id)).toBe(false);
  });

  it("never leads two items with the same reference video", () => {
    const twin = { ...recommendations[0], id: "rec_twin" };
    const items = composeBrief({ ...base, news: [], refresh: [], recommendations: [recommendations[0], twin] });
    expect(items.map((item) => item.id)).toEqual([recommendations[0].id]);
  });

  it("keeps a thin-data notice without a wider window out of the brief", () => {
    const items = composeBrief({ ...base, news: [], refresh: [], recommendations: recommendations.slice(0, 1), thinNotice: { code: "thin_data", level: "info", message: "No pattern yet." } });
    expect(items.some((item) => item.id === "thin")).toBe(false);
  });

  it("offers widening when there are fewer than three items and a thin-data notice", () => {
    const items = composeBrief({ ...base, news: [], refresh: [], recommendations: recommendations.slice(0, 1), thinNotice: { code: "thin_data", level: "info", message: "Not enough outliers in 7 days.", suggest: { widen_window_to: 30 } } });
    expect(items.at(-1)).toMatchObject({ id: "thin", actionLabel: "Widen to 30 days" });
  });

  it("fills an overflow list for Also worth a look without changing the first five", () => {
    const five = composeBrief({ ...base, recommendations });
    const more = composeBrief({ ...base, recommendations, limit: 10 });
    expect(more.slice(0, 5).map((item) => item.id)).toEqual(five.map((item) => item.id));
    expect(more.length).toBeGreaterThan(5);
  });
});
