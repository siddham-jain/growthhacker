import { describe, expect, it } from "vitest";
import type { BusinessProfile, CompetitorView, KeywordItem, Page, ProfileFact } from "@/lib/api/types";
import { applyProfilePatch, applyTopicEdit, checkFirst, competitorBlocker, factCheckReason, groupCompetitors, keywordCheckReason, previewItems, upsertById } from "./edits";
import { marketLine } from "./market";

function fact(id: string, label: string, sourceKind: ProfileFact["meta"]["evidence"][number]["source_kind"] = "site_direct", confidence = 0.9): ProfileFact {
  return { id, label, meta: { origin: "extracted", status: "proposed", confidence, evidence: [{ url: "https://example.com", quote: label, source_kind: sourceKind, fetched_at: "2026-11-20T00:00:00Z" }] } };
}

const profile = {
  identity: { name: "LeRoy", one_liner: "Packaging", industry: "manufacturing", niche: "packaging", country: "IN", timezone: "Asia/Kolkata", website_domains: [], brand_aliases: [], one_liner_meta: fact("x", "x").meta },
  offerings: [fact("o1", "Cartons"), fact("o2", "Boxes")],
  value_propositions: [],
  locations: [],
  conversion_actions: [],
  freshness: { sensitivity: "low", regulated_domain: false, domain_kind: "none", rationale: "" },
} as BusinessProfile;

function keyword(id: string, text: string, source = "page_text", status: KeywordItem["status"] = "proposed"): KeywordItem {
  return { id, text, status, source };
}

describe("onboarding edits", () => {
  it("removes, corrects, adds, confirms and restores profile facts", () => {
    const removed = applyProfilePatch(profile, { facts: [{ group: "offerings", op: "remove", id: "o1" }] });
    expect(removed.offerings.map((item) => item.id)).toEqual(["o2"]);
    const restored = applyProfilePatch(removed, { facts: [{ group: "offerings", op: "restore", fact: profile.offerings[0] }] });
    expect(restored.offerings.map((item) => item.label)).toEqual(["Boxes", "Cartons"]);
    const edited = applyProfilePatch(profile, { facts: [{ group: "offerings", op: "edit", id: "o2", label: "Corrugated boxes" }, { group: "offerings", op: "confirm", id: "o1" }] });
    expect(edited.offerings.map((item) => [item.label, item.meta.status])).toEqual([["Cartons", "confirmed"], ["Corrugated boxes", "edited"]]);
    const added = applyProfilePatch(profile, { facts: [{ group: "locations", op: "add", id: "user_1", label: "Ludhiana depot" }], identity: { one_liner: "Cartons and boxes", country: "IN" } });
    expect(added.locations).toMatchObject([{ id: "user_1", label: "Ludhiana depot", meta: { origin: "user", status: "confirmed" } }]);
    expect(added.identity.one_liner).toBe("Cartons and boxes");
    expect(profile.offerings).toHaveLength(2);
  });

  it("flags third-party and low-confidence facts and lists them first", () => {
    const facts = [fact("a", "Read on site"), fact("b", "From the archive", "archive"), fact("c", "Unsure", "site_direct", 0.4), { ...fact("d", "Mine", "archive"), meta: { ...fact("d", "Mine", "archive").meta, status: "edited" as const } }];
    expect(facts.map(factCheckReason)).toEqual([null, "From web archive, not your site", "Low confidence", null]);
    expect(checkFirst(facts, factCheckReason).map((item) => item.id)).toEqual(["b", "c", "a", "d"]);
    expect(keywordCheckReason(keyword("k", "suggested", "llm_expansion"))).not.toBeNull();
  });

  it("marks topics rejected on remove and confirmed on restore or confirm", () => {
    const page: Page<KeywordItem> = { items: [keyword("k1", "cartons"), keyword("k2", "boxes")], next_cursor: null, limit: 100, total: 2 };
    expect(applyTopicEdit(page, { op: "remove", id: "k1" }).items[0].status).toBe("rejected");
    expect(applyTopicEdit(page, { op: "restore", id: "k1" }).items[0].status).toBe("confirmed");
    expect(applyTopicEdit(page, { op: "edit", id: "k2", text: "corrugated boxes" }).items[1].text).toBe("corrugated boxes");
    expect(applyTopicEdit(page, { op: "confirm", ids: ["k1", "k2"] }).items.every((item) => item.status === "confirmed")).toBe(true);
    const replaced = upsertById(page, keyword("k9", "cartons", "user", "confirmed"), "k1");
    expect(replaced.items.map((item) => item.id)).toEqual(["k9", "k2"]);
    expect(upsertById(page, keyword("k3", "labels")).items).toHaveLength(3);
  });

  it("shows the top of a long list and keeps short lists whole", () => {
    const items = Array.from({ length: 25 }, (_, index) => index);
    expect(previewItems(items, false)).toEqual({ shown: items.slice(0, 8), hidden: 17 });
    expect(previewItems(items, true).hidden).toBe(0);
    expect(previewItems(items.slice(0, 10), false).hidden).toBe(0);
    expect(previewItems(items, false, (item) => item === 24)).toEqual({ shown: [...items.slice(0, 8), 24], hidden: 16 });
  });

  it("splits competitors into direct, also watch and channel-less, and enforces 3–10", () => {
    const competitor = (id: string, role: CompetitorView["role"], status: CompetitorView["status"]) => ({ id, role, status }) as CompetitorView;
    const groups = groupCompetitors([
      competitor("a", "competitor_set", "confirmed"),
      competitor("b", "competitor_set", "rejected"),
      competitor("c", "niche_set", "suggested"),
      competitor("d", "niche_set", "no_channel"),
      competitor("e", "excluded", "excluded"),
    ]);
    expect([groups.direct, groups.watch, groups.noChannel].map((list) => list.map((item) => item.id))).toEqual([["a"], ["c"], ["d"]]);
    expect(competitorBlocker(2)).toMatch(/at least 3/);
    expect(competitorBlocker(11)).toMatch(/up to 10/);
    expect(competitorBlocker(3)).toBeNull();
  });

  it("names the market with the first physical place", () => {
    const office = { ...fact("l1", "Head office"), detail: "Hoshiarpur, Punjab", kind: "office" };
    const area = { ...fact("l0", "Dispatch across India"), detail: "India", kind: "service_area" };
    const branch = { ...fact("l2", "Pune branch"), detail: "Pune", kind: "branch" };
    expect(marketLine("IN", [area, branch, office])).toBe("India · Hoshiarpur, Punjab");
    expect(marketLine("IN", [area, branch])).toBe("India · Pune");
    expect(marketLine("", [])).toBe("Market not detected yet");
  });
});
