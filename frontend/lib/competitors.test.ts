import { describe, expect, it } from "vitest";
import type { CompetitorChannelView } from "@/lib/api/types";
import { formatDate } from "@/lib/format";
import { channelFacts } from "./competitors";

const channel = (overrides: { uploads_90d: number; matching: number | null; checked: number | null; last: string | null }) =>
  ({
    evidence: { uploads_90d: overrides.uploads_90d, recent_upload_ids: [], last_upload_at: overrides.last },
    relevance: { matched_topic_refs: [], matching_uploads: overrides.matching, uploads_checked: overrides.checked },
  }) as unknown as CompetitorChannelView;

describe("channelFacts", () => {
  it("states raw upload counts and never a score", () => {
    const facts = channelFacts(channel({ uploads_90d: 12, matching: 7, checked: 50, last: "2026-11-18T10:00:00Z" }));
    expect(facts).toEqual(["12 uploads in the last 90 days", "7 of its last 50 uploads match your topics", `last upload ${formatDate("2026-11-18T10:00:00Z")}`]);
    expect(facts.join(" ")).not.toMatch(/0\.\d/);
  });

  it("says plainly when nothing matched or nothing was read", () => {
    expect(channelFacts(channel({ uploads_90d: 1, matching: 0, checked: 3, last: null }))).toEqual(["1 upload in the last 90 days", "0 of its last 3 uploads match your topics", "no uploads yet"]);
    expect(channelFacts(channel({ uploads_90d: 0, matching: null, checked: null, last: null }))[1]).toBe("no recent uploads read");
  });
});
