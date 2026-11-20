import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RecommendationEvidence, VideoEvidence } from "@/components/evidence/evidence-drawer";
import { videos } from "@/lib/api/mock/fixtures/videos";
import { recommendations } from "@/lib/api/mock/fixtures/insights";

describe("evidence", () => {
  it("lists every reference video with views at age, method, baseline maths and the disclosure", () => {
    const rec = recommendations[0];
    render(<RecommendationEvidence rec={rec} />);
    expect(screen.getByText(`Reference videos · ${rec.reference_videos.length}`)).toBeInTheDocument();
    for (const video of rec.reference_videos) expect(screen.getByText(video.title)).toBeInTheDocument();
    expect(screen.getByText("41,200 vs channel normal 8,600 at day 7 → 4.8×")).toBeInTheDocument();
    expect(screen.getAllByText("Observed").length).toBeGreaterThan(0);
    expect(screen.getByText(/Calculated by GrowthHacker/)).toBeInTheDocument();
    expect(screen.getByText(/Found 6 outliers across 4 channels/)).toBeInTheDocument();
  });

  it("states caps in words and labels a missing range honestly", () => {
    render(<RecommendationEvidence rec={{ ...recommendations[3], expected_range_vs_baseline: null }} />);
    expect(screen.getByText("Capped at Low: one channel supplies most references")).toBeInTheDocument();
    expect(screen.getByText("Not enough comparable videos for a range.")).toBeInTheDocument();
  });

  it("marks a proxied video as an estimate", () => {
    render(<VideoEvidence video={videos.apolloCheckup} />);
    expect(screen.getByText("Proxy")).toBeInTheDocument();
    expect(screen.getByText(/Treat the ratio as a rough guide/)).toBeInTheDocument();
  });
});
