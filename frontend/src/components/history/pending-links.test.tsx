import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { eventsApi } from "@/lib/api";
import { PendingLinks } from "./pending-links";

vi.mock("@/lib/api", () => ({
  eventsApi: {
    pendingLinks: vi.fn(),
    verifyLink: vi.fn(),
  },
}));

const api = eventsApi as unknown as {
  pendingLinks: Mock;
  verifyLink: Mock;
};

const pending = [
  {
    event_id: "evt-1",
    event_title: "Will the Fed cut rates?",
    event_resolution_criteria: "Official Fed decision.",
    market_name: "Kalshi",
    contract_id: "FEDCUT-26",
    market_question: "Fed cuts rates in 2026?",
    resolution_criteria: "Kalshi settlement.",
    link_confidence: 0.87,
    linked_at: "2026-06-23T00:00:00Z",
  },
  {
    event_id: "evt-2",
    event_title: "Will CPI fall below 2%?",
    event_resolution_criteria: "Official CPI release.",
    market_name: "Polymarket",
    contract_id: "CPI-2",
    market_question: "CPI below 2%?",
    resolution_criteria: "Market settlement.",
    link_confidence: 0.72,
    linked_at: "2026-06-23T00:10:00Z",
  },
];

describe("PendingLinks", () => {
  beforeEach(() => {
    api.pendingLinks.mockReset();
    api.verifyLink.mockReset();
    api.pendingLinks.mockResolvedValue({ pending });
    api.verifyLink.mockResolvedValue({});
  });

  it("removes only the verified pending link by stable event/contract key", async () => {
    render(<PendingLinks />);

    expect(await screen.findByText("Will the Fed cut rates?")).toBeInTheDocument();
    expect(screen.getByText("Will CPI fall below 2%?")).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: "确认关联" })[0]);

    await waitFor(() => expect(api.verifyLink).toHaveBeenCalledWith("evt-1", "FEDCUT-26"));
    await waitFor(() => expect(screen.queryByText("Will the Fed cut rates?")).not.toBeInTheDocument());
    expect(screen.getByText("Will CPI fall below 2%?")).toBeInTheDocument();
  });

  it("keeps the pending link visible when verification fails", async () => {
    api.verifyLink.mockRejectedValue(new Error("Missing or invalid API key"));

    render(<PendingLinks />);

    expect(await screen.findByText("Will the Fed cut rates?")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "确认关联" })[0]);

    expect(await screen.findByText("Missing or invalid API key")).toBeInTheDocument();
    expect(screen.getByText("Will the Fed cut rates?")).toBeInTheDocument();
  });
});
