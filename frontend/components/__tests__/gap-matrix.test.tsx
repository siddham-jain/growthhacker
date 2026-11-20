import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GapMatrix } from "@/components/charts/gap-matrix";
import type { GapMatrixView } from "@/lib/api/types";

const matrix: GapMatrixView = {
  topics: [
    { id: "q1", label: "How much does it cost?" },
    { id: "q2", label: "How long is recovery?" },
  ],
  channels: [
    { channel_id: "c1", title: "Fortis Healthcare" },
    { channel_id: "c2", title: "Medanta" },
  ],
  cells: { q1: { c1: { n_videos: 3, n_outliers: 1, best_ratio: 4.2 }, c2: { n_videos: 0, n_outliers: 0, best_ratio: null } }, q2: { c1: { n_videos: 0, n_outliers: 0, best_ratio: null }, c2: { n_videos: 0, n_outliers: 0, best_ratio: null } } },
  own: { q1: 0, q2: 0 },
};

describe("GapMatrix", () => {
  it("labels every cell with its counts and flags questions only competitors cover", () => {
    render(<GapMatrix matrix={matrix} clientId="cl" />);
    expect(screen.getByRole("link", { name: "Fortis Healthcare, How much does it cost?: 3 videos, 1 outlier" })).toHaveAttribute("href", "/c/cl/competitors/c1?tab=gap");
    expect(screen.getAllByText("gap")).toHaveLength(1);
  });
});
