import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrendingIdeas } from "@/components/trends/trending-ideas";
import { envelope } from "@/lib/api/mock/fixtures/base";
import { trendScan } from "@/lib/api/mock/fixtures/trends";
import { trendsQuery } from "@/lib/api/queries";
import type { TrendScan } from "@/lib/api/types";

function renderWith(scan: TrendScan) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(trendsQuery("cl").queryKey, envelope(scan, 30));
  return render(
    <QueryClientProvider client={client}>
      <TrendingIdeas clientId="cl" />
    </QueryClientProvider>,
  );
}

describe("trending ideas", () => {
  it("leads with ideas and shows the first one's evidence", () => {
    renderWith(trendScan());
    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(3);
    const first = within(cards[0]);
    expect(first.getByText("Is robotic knee surgery worth it? An orthopaedic surgeon answers")).toBeInTheDocument();
    expect(first.getByText("Trending")).toBeInTheDocument();
    expect(first.getByText(/Two insurers announced cover/)).toBeInTheDocument();
    expect(first.getByText("Robotic knee surgery explained in Hindi")).toBeInTheDocument();
  });

  it("lists every checked keyword with where it came from", () => {
    renderWith(trendScan());
    const section = screen.getByRole("region", { name: "Keywords we checked on YouTube" });
    expect(within(section).getAllByRole("listitem").filter((item) => item.parentElement?.classList.contains("divide-y"))).toHaveLength(4);
    expect(within(section).getAllByText("In the news")).toHaveLength(1);
    fireEvent.click(within(section).getByText("heart attack in young people"));
    expect(within(section).getByText("Why are 30-year-olds having heart attacks?")).toBeInTheDocument();
  });

  it("explains what happens before the first scan", () => {
    renderWith({ as_of: "2026-11-20T00:00:00Z", window_days: 14, keywords: [], ideas: [], search_calls: 0, youtube_units: 0, llm_calls: 0, tavily_credits: 0, messages: ["Trends appear after your first analysis finishes."] });
    expect(screen.getByText("Trends appear after your first analysis finishes.")).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
});
