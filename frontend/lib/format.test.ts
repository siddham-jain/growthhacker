import { describe, expect, it } from "vitest";
import { formatCompact, formatCountdown, formatExact, formatRatio, plural } from "./format";

describe("format", () => {
  it("formats ratios with one decimal and says when there is no baseline", () => {
    expect(formatRatio(3.62)).toBe("3.6×");
    expect(formatRatio(0.7)).toBe("0.7×");
    expect(formatRatio(null)).toBe("no baseline yet");
  });

  it("uses compact K/M everywhere and indian grouping for exact values", () => {
    expect(formatCompact(18_400)).toBe("18.4K");
    expect(formatCompact(1_200_000)).toBe("1.2M");
    expect(formatCompact(null)).toBe("–");
    expect(formatExact(112_000)).toBe("1,12,000");
  });

  it("counts news windows down in hours, then days", () => {
    const now = Date.parse("2026-11-20T00:00:00Z");
    expect(formatCountdown("2026-11-20T10:00:00Z", now)).toBe("closes in 10 h");
    expect(formatCountdown("2026-11-24T00:00:00Z", now)).toBe("closes in 4 days");
    expect(formatCountdown("2026-11-19T00:00:00Z", now)).toBe("closed");
  });

  it("pluralises", () => {
    expect(plural(1, "outlier")).toBe("1 outlier");
    expect(plural(3, "outlier")).toBe("3 outliers");
  });
});
