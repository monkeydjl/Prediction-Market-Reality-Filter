import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MarketLinksTable } from "./MarketLinksTable";
import type { MarketLinkList } from "@/lib/sport-markets-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({ fetchMarketLinks: vi.fn() }));
vi.mock("@/lib/sport-markets-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-markets-api")>()),
  fetchMarketLinks: apiMocks.fetchMarketLinks,
}));

const linksData: MarketLinkList = {
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

  it("renders rows after load", async () => {
    apiMocks.fetchMarketLinks.mockResolvedValue(linksData);
    render(<MarketLinksTable />);
    await waitFor(() =>
      expect(screen.getByTestId("market-links-table")).toBeInTheDocument(),
    );
    expect(screen.getByText("m1")).toBeInTheDocument();
  });

  it("shows verified badge text", async () => {
    apiMocks.fetchMarketLinks.mockResolvedValue(linksData);
    render(<MarketLinksTable />);
    await waitFor(() => expect(screen.getByTestId("badge-1")).toBeInTheDocument());
    expect(screen.getByTestId("badge-1").textContent).toBe("已验证");
  });

  it("renders empty state", async () => {
    apiMocks.fetchMarketLinks.mockResolvedValue({ items: [], total: 0 });
    render(<MarketLinksTable />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.fetchMarketLinks.mockRejectedValue(new Error("boom"));
    render(<MarketLinksTable />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });
});
