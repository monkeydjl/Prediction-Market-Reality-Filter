import { render, screen } from "@testing-library/react";
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
});
