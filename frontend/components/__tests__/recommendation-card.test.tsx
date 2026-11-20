import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RecommendationCard } from "@/components/cards/recommendation-card";
import { recommendations } from "@/lib/api/mock/fixtures/insights";

const rec = recommendations[0];

function setup(overrides: Partial<typeof rec> = {}) {
  const onFeedback = vi.fn();
  const onEvidence = vi.fn();
  render(<RecommendationCard recommendation={{ ...rec, ...overrides }} lensFilter="both" onFeedback={onFeedback} onEvidence={onEvidence} />);
  return { onFeedback, onEvidence, user: userEvent.setup() };
}

describe("RecommendationCard", () => {
  it("shows the summary, buyer question and confidence with its sample size as text", () => {
    setup();
    expect(screen.getByRole("heading", { name: rec.summary })).toBeInTheDocument();
    expect(screen.getByText(`“${rec.buyer_question}”`)).toBeInTheDocument();
    expect(screen.getByText(/6 outliers · 4 channels/)).toBeInTheDocument();
  });

  it("expands in place to every reason, the fit and the indicative range", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: /More evidence/ }));
    expect(screen.getByText(rec.why_it_works[1])).toBeInTheDocument();
    expect(screen.getByText(rec.expected_range_vs_baseline ?? "")).toBeInTheDocument();
  });

  it("saves, and opens the evidence drawer", async () => {
    const { user, onFeedback, onEvidence } = setup();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(onFeedback).toHaveBeenCalledWith({ status: "saved" });
    await user.click(screen.getByRole("button", { name: "Evidence" }));
    expect(onEvidence).toHaveBeenCalled();
  });

  it("dismisses with a reason", async () => {
    const { user, onFeedback } = setup();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    const dialog = await screen.findByRole("dialog", { name: "Why dismiss?" });
    await user.click(within(dialog).getByRole("button", { name: "We already covered this" }));
    expect(onFeedback).toHaveBeenCalledWith({ status: "dismissed", reason: "already_covered" });
  });

  it("marks done with a linked video and rejects links that aren't youtube", async () => {
    const { user, onFeedback } = setup();
    await user.click(screen.getByRole("button", { name: "Mark done" }));
    const dialog = await screen.findByRole("dialog", { name: "Mark done" });
    const field = within(dialog).getByLabelText("Your video link (optional)");
    await user.type(field, "https://vimeo.com/1");
    await user.click(within(dialog).getByRole("button", { name: "Mark done" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Paste a YouTube video link");
    await user.clear(field);
    await user.type(field, "https://youtu.be/dQw4w9WgXcQ");
    await user.click(within(dialog).getByRole("button", { name: "Mark done" }));
    expect(onFeedback).toHaveBeenCalledWith({ status: "done", linked_video_id: "dQw4w9WgXcQ" });
  });

  it("collapses to an undo row once dismissed", async () => {
    const { user, onFeedback } = setup({ feedback: { status: "dismissed", reason: "other" } });
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(onFeedback).toHaveBeenCalledWith({ status: null });
  });
});
