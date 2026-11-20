import { describe, expect, it } from "vitest";
import type { ProfileSource } from "@/lib/api/types";
import { groupSources, pageLabel } from "./sources";

const page = (url: string, source_kind: ProfileSource["source_kind"], claims: number): ProfileSource => ({ url, source_kind, claims, fetched_at: null, snapshot_at: null });

describe("groupSources", () => {
  it("collapses pages into one group per source kind, in cascade order", () => {
    const groups = groupSources([
      page("https://www.medanta.org/", "site_direct", 56),
      page("https://en.wikipedia.org/wiki/Medanta", "knowledge_base", 4),
      page("https://www.medanta.org/treatment", "site_direct", 46),
      page("https://r.jina.ai/https://www.medanta.org/x", "reader_proxy", 3),
    ]);
    expect(groups.map((group) => [group.kind, group.pages.length, group.facts])).toEqual([
      ["site_direct", 2, 102],
      ["reader_proxy", 1, 3],
      ["knowledge_base", 1, 4],
    ]);
  });

  it("marks a site tier blocked only when none of its pages gave facts", () => {
    expect(groupSources([page("https://a.example/", "site_direct", 0)])[0].blocked).toBe(true);
    expect(groupSources([page("https://a.example/", "site_direct", 0), page("https://a.example/b", "site_direct", 2)])[0].blocked).toBe(false);
    expect(groupSources([page("https://a.example/", "archive", 0)])[0].blocked).toBe(false);
  });
});

describe("pageLabel", () => {
  it.each([
    ["https://www.medanta.org/", "medanta.org"],
    ["https://www.medanta.org/treatment/", "medanta.org/treatment"],
    ["not a url", "not a url"],
  ])("labels %s", (url, expected) => expect(pageLabel(url)).toBe(expected));
});
