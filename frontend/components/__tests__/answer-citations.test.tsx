import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { CompetitorAnswerCitations, YourAnswerCitations } from "@/components/citations/answer-citations";
import { answerCitations } from "@/lib/api/mock/fixtures/citations";
import { answerCitationsQuery } from "@/lib/api/queries";
import type { AnswerCitations } from "@/lib/api/types";
import { METRIC_DISCLOSURE } from "@/lib/labels";

function renderWith(data: AnswerCitations, ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(answerCitationsQuery("cl").queryKey, data);
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("answer citations", () => {
  it("lists gaps first and labels every count as ours", () => {
    renderWith(answerCitations(), <YourAnswerCitations clientId="cl" />);
    const section = screen.getByRole("region", { name: "Cited in AI answers" });
    const rows = within(section).getAllByRole("listitem").filter((item) => item.querySelector("p.text-body-medium"));
    expect(rows[0]).toHaveTextContent("Do I need angioplasty or bypass surgery?");
    expect(rows[0]).toHaveTextContent("Gap: a competitor, not you");
    expect(within(section).getByText(/Your videos are cited for 1 of 5 buyer questions/)).toBeInTheDocument();
    expect(within(section).getByText(METRIC_DISCLOSURE)).toBeInTheDocument();
  });

  it("shows competitors alphabetically with the questions that cite them", () => {
    renderWith(answerCitations(), <CompetitorAnswerCitations clientId="cl" names={{}} />);
    const names = screen.getAllByText(/cited for \d question/).map((node) => node.parentElement?.firstChild?.textContent);
    expect(names).toEqual(["Fortis Healthcare", "Medanta", "Narayana Health"]);
    expect(screen.getByText(/Gaps: a competitor is cited and you aren't · 2/)).toBeInTheDocument();
  });

  it("explains the weekly check before anything was checked", () => {
    const empty = { ...answerCitations(), last_checked_at: null, questions: [], competitors: [], counts: { questions_checked: 0, cite_you: 0, cite_competitors: 0, competitor_only: 0, no_tracked_channel: 0 } };
    renderWith(empty, <YourAnswerCitations clientId="cl" />);
    expect(screen.getByText(/search up to 10 of your top questions/)).toBeInTheDocument();
    expect(screen.queryByText(METRIC_DISCLOSURE)).not.toBeInTheDocument();
  });
});
