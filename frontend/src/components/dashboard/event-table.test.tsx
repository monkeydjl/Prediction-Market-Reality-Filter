import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { EventTable } from "./event-table";
import type { EventView } from "@/lib/adapt";

const events: EventView[] = [
  {
    id: "evt-1",
    title: "Federal Reserve rate cut",
    description: "monetary policy",
    category: "monetary",
    currentProbability: 60,
    baselineProbability: 50,
    delta: 10,
    direction: "up",
    evidenceSupport: 0.8,
    priority: "high",
    trackingStatus: "tracking",
    trend: "up",
    valueScore: 90,
  },
  {
    id: "evt-2",
    title: "Ethereum ETF approval",
    description: "crypto market",
    category: "crypto",
    currentProbability: 35,
    baselineProbability: 45,
    delta: -10,
    direction: "down",
    evidenceSupport: 0.4,
    priority: "medium",
    trackingStatus: "watching",
    trend: "down",
    valueScore: 50,
  },
];

describe("EventTable", () => {
  it("filters events by text search", async () => {
    render(<EventTable events={events} total={2} />);

    await userEvent.type(screen.getByLabelText("搜索事件"), "ethereum");

    expect(screen.getByText("Ethereum ETF approval")).toBeInTheDocument();
    expect(screen.queryByText("Federal Reserve rate cut")).not.toBeInTheDocument();
  });

  it("restores filters from the URL and keeps changes in the URL", async () => {
    window.history.replaceState(null, "", "/?q=ethereum&status=watching&sort=probability");
    render(<EventTable events={events} total={2} />);

    await waitFor(() => expect(screen.getByLabelText("搜索事件")).toHaveValue("ethereum"));
    expect(screen.getByText("Ethereum ETF approval")).toBeInTheDocument();
    expect(screen.queryByText("Federal Reserve rate cut")).not.toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("搜索事件"));

    await waitFor(() => expect(window.location.search).not.toContain("q="));
    expect(window.location.search).toContain("status=watching");
    expect(window.location.search).toContain("sort=probability");
  });
});
