import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const apiMocks = vi.hoisted(() => ({
  useMarketLinks: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useMarketLinks: apiMocks.useMarketLinks,
}));

import { MarketLinksTable } from "./MarketLinksTable";

const linksData = {
  items: [
    {
      id: 1, match_id: "m1", contract_id: "c1", source: "polymarket",
      outcome_label: "YES", mapped_outcome: "home_win", link_method: "rule",
      link_confidence: 0.95, verified: true, market_question: "Will Lakers win?",
      implied_prob: 0.6,
    },
  ],
  total: 1,
};

describe("MarketLinksTable", () => {
  beforeEach(() => {
    apiMocks.useMarketLinks.mockReset();
  });

  it("renders rows after load", async () => {
    apiMocks.useMarketLinks.mockReturnValue({
      data: linksData,
      error: undefined,
      isLoading: false,
    });
    render(<MarketLinksTable />);
    await waitFor(() =>
      expect(screen.getByTestId("market-links-table")).toBeInTheDocument(),
    );
    expect(screen.getByText("m1")).toBeInTheDocument();
  });

  it("shows verified badge text", async () => {
    apiMocks.useMarketLinks.mockReturnValue({
      data: linksData,
      error: undefined,
      isLoading: false,
    });
    render(<MarketLinksTable />);
    await waitFor(() => expect(screen.getByTestId("badge-1")).toBeInTheDocument());
    expect(screen.getByTestId("badge-1").textContent).toBe("已验证");
  });

  it("renders empty state", async () => {
    apiMocks.useMarketLinks.mockReturnValue({
      data: { items: [], total: 0 },
      error: undefined,
      isLoading: false,
    });
    render(<MarketLinksTable />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.useMarketLinks.mockReturnValue({
      data: undefined,
      error: new Error("boom"),
      isLoading: false,
    });
    render(<MarketLinksTable />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });
});
