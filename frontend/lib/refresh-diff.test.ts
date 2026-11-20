import { describe, expect, it } from "vitest";
import { unifiedDiff } from "@/lib/api/mock/fixtures/base";
import { refreshSuggestions } from "@/lib/api/mock/fixtures/insights";
import { addedText, highlightKeywords, parseUnifiedDiff } from "./refresh-diff";

const description = ["Intro", "Second", "", "a", "b", "c", "d", "e", "f", "", "#Tags"].join("\n");

describe("parseUnifiedDiff", () => {
  it("reads additions, context and the gaps between hunks", () => {
    const after = description.replace("Second\n", "Second\nNEW LINE\n");
    const rows = parseUnifiedDiff(unifiedDiff(description, after), []);
    expect(rows.filter((row) => row.kind === "insert").map((row) => row.text)).toEqual(["NEW LINE"]);
    expect(rows[0]).toEqual({ kind: "context", text: "Intro" });
    expect(rows.some((row) => row.kind === "link_fix")).toBe(false);
  });

  it("starts with a gap when the first hunk isn't at line one", () => {
    const after = `${description}\nAPPENDED`;
    const rows = parseUnifiedDiff(unifiedDiff(description, after), []);
    expect(rows[0]).toMatchObject({ kind: "gap" });
    expect(addedText(rows)).toBe("APPENDED");
  });

  it("pairs a removed and added line as the one allowed link fix, matched to its operation", () => {
    const fix = refreshSuggestions[2];
    const rows = parseUnifiedDiff(fix.preview_diff, fix.operations);
    const row = rows.find((item) => item.kind === "link_fix");
    expect(row).toMatchObject({ oldText: "https://www.apollohospitals.com/navimumbai-old", text: "https://www.apollohospitals.com/locations/navi-mumbai" });
    expect(row && "op" in row ? row.op?.op_id : null).toBe("op3");
  });

  it("matches insertions to their operation for the position label and keywords", () => {
    const suggestion = refreshSuggestions[0];
    const insert = parseUnifiedDiff(suggestion.preview_diff, suggestion.operations).find((row) => row.kind === "insert");
    expect(insert && "op" in insert ? insert.op?.position : null).toBe("after_first_paragraph");
  });

  it("highlights added keywords case-insensitively", () => {
    expect(highlightKeywords("Now for Brain Tumours too", ["brain tumours"])).toEqual([
      { text: "Now for ", mark: false },
      { text: "Brain Tumours", mark: true },
      { text: " too", mark: false },
    ]);
  });
});
