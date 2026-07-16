import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const apiMocks = vi.hoisted(() => ({
  usePendingLinks: vi.fn(),
  verifyLink: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  usePendingLinks: apiMocks.usePendingLinks,
  verifyLink: apiMocks.verifyLink,
}));

import { PendingReviewQueue } from "./PendingReviewQueue";

const pendingData = {
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

const makePendingResult = (overrides: Partial<typeof pendingData> = {}) => ({
  data: { ...pendingData, ...overrides },
  error: undefined,
  isLoading: false,
  mutate: vi.fn().mockResolvedValue(undefined),
});

describe("PendingReviewQueue", () => {
  beforeEach(() => {
    apiMocks.usePendingLinks.mockReset();
    apiMocks.verifyLink.mockReset();
  });

  it("renders pending cards", async () => {
    apiMocks.usePendingLinks.mockReturnValue(makePendingResult());
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("card-1")).toBeInTheDocument());
    expect(screen.getByText("Will Lakers win?")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.usePendingLinks.mockReturnValue({
      data: { items: [], total: 0 },
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    });
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("confirm button calls verifyLink with true", async () => {
    apiMocks.usePendingLinks.mockReturnValue(makePendingResult());
    apiMocks.verifyLink.mockResolvedValue(undefined);
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("confirm-1")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("confirm-1"));
    await waitFor(() =>
      expect(apiMocks.verifyLink).toHaveBeenCalledWith("m1", "c1", true),
    );
  });

  it("reject button calls verifyLink with false", async () => {
    apiMocks.usePendingLinks.mockReturnValue(makePendingResult());
    apiMocks.verifyLink.mockResolvedValue(undefined);
    render(<PendingReviewQueue />);
    await waitFor(() => expect(screen.getByTestId("reject-1")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("reject-1"));
    await waitFor(() =>
      expect(apiMocks.verifyLink).toHaveBeenCalledWith("m1", "c1", false),
    );
  });
});
