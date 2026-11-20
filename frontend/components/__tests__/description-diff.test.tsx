import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RefreshCard } from "@/components/cards/refresh-card";
import { refreshSuggestions } from "@/lib/api/mock/fixtures/insights";

describe("RefreshCard", () => {
  it("highlights the additive insertion inside the current description", () => {
    render(<RefreshCard suggestion={refreshSuggestions[0]} onApplied={vi.fn()} onDismiss={vi.fn()} />);
    const list = screen.getByRole("list", { name: "Description with suggested additions" });
    expect(list).toHaveTextContent("Proton therapy targets tumours");
    expect(screen.getByText("children's brain tumours", { selector: "mark" })).toBeInTheDocument();
    expect(screen.getByText("After the first paragraph")).toBeInTheDocument();
  });

  it("copies a line, then marks it applied with the added text", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    const onApplied = vi.fn();
    const suggestion = refreshSuggestions[0];
    render(<RefreshCard suggestion={suggestion} onApplied={onApplied} onDismiss={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Copy line" }));
    expect(writeText).toHaveBeenLastCalledWith(suggestion.operations[0].text);
    await user.click(screen.getByRole("button", { name: "Mark applied" }));
    expect(onApplied).toHaveBeenCalledWith(suggestion.operations[0].text);
  });

  it("shows a link fix as the only struck-through text", () => {
    render(<RefreshCard suggestion={refreshSuggestions[2]} onApplied={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.getByText("https://www.apollohospitals.com/navimumbai-old")).toHaveClass("line-through");
  });

  it("doesn't diff against a description that changed since", () => {
    render(<RefreshCard suggestion={{ ...refreshSuggestions[0], description_changed: true, preview_diff: "" }} onApplied={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.getByText(/Your description changed since we wrote this/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark applied" })).toBeDisabled();
  });

  it("collapses to a receipt once applied", () => {
    render(<RefreshCard suggestion={{ ...refreshSuggestions[1], status: "applied", applied_at: "2026-11-20T00:00:00Z" }} onApplied={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.getByText("Applied, we'll track the effect")).toBeInTheDocument();
  });
});
