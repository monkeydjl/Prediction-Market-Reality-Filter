import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PendingReviewQueue } from "./PendingReviewQueue";
import type { MarketLinkList } from "@/lib/sport-markets-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({
  fetchPendingLinks: vi.fn(),
  verifyLink: vi.fn(),
}));
vi.mock("@/lib/sport-markets-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-markets-api")>()),
  fetchPendingLinks: apiMocks.fetchPendingLinks,
  verifyLink: apiMocks.verifyLink,
}));

const pendingData: MarketLinkList = {
  items: [
    {
      id: 1, match_id: "m1", contract_id: "c1", source: "polymarket",
      outcome_label: "YES", mapped_outcome: "home_win", link_method: "llm",
      link_confidence: 0.7, verified: false, market_question: "Will Lakers win?",
      implied_prob: 0.55,
    },
  ],
  total: 1,
};

describe("PendingReviewQueue", () => {
  beforeEach(() => {
    apiMocks.fetchPendingLinks.mockReset();
    apiMocks.verifyLink.mockReset();
  });

  it("renders pending cards", async () => {
    apiMocks.fetchPendingLinks.mockResolvedValue(pendingData);
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("card-1")).toBeInTheDocument());
    expect(screen.getByText("Will Lakers win?")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.fetchPendingLinks.mockResolvedValue({ items: [], total: 0 });
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("confirm button calls verifyLink with true", async () => {
    apiMocks.fetchPendingLinks.mockResolvedValue(pendingData);
    apiMocks.verifyLink.mockResolvedValue(undefined);
    apiMocks.fetchPendingLinks
      .mockResolvedValueOnce(pendingData)
      .mockResolvedValueOnce({ items: [], total: 0 });
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("confirm-1")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("confirm-1"));
    await waitFor(() =>
      expect(apiMocks.verifyLink).toHaveBeenCalledWith("m1", "c1", true),
    );
  });

  it("reject button calls verifyLink with false", async () => {
    apiMocks.fetchPendingLinks.mockResolvedValue(pendingData);
    apiMocks.verifyLink.mockResolvedValue(undefined);
    apiMocks.fetchPendingLinks
      .mockResolvedValueOnce(pendingData)
      .mockResolvedValueOnce({ items: [], total: 0 });
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("reject-1")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("reject-1"));
    await waitFor(() =>
      expect(apiMocks.verifyLink).toHaveBeenCalledWith("m1", "c1", false),
    );
  });
});
