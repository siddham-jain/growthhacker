import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NoticeList } from "@/components/states/notice-list";

describe("NoticeList", () => {
  it("uses card wording copy only for card stages, the server message elsewhere, and shows each line once", () => {
    render(
      <NoticeList
        notices={[
          { code: "llm_fallback", stage: "recommend", message: "1 cards used plainer template wording" },
          { code: "llm_fallback", stage: "write_cards", message: "2 cards used plainer template wording" },
          { code: "llm_fallback", stage: "topics", message: "Some borderline topic matches will be checked later." },
          { code: "thin_data", stage: "analytics", message: "hidden" },
        ]}
      />,
    );
    expect(screen.getByText("Some cards use plain wording today.")).toBeInTheDocument();
    expect(screen.getByText("Some borderline topic matches will be checked later.", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });
});
