import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
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

const eventsWithSports: EventView[] = [
  ...events,
  {
    id: "evt-3",
    title: "Brazil World Cup semifinal",
    description: "2026 FIFA World Cup",
    category: "sports_event",
    currentProbability: 38,
    baselineProbability: 35,
    delta: 3,
    direction: "up",
    evidenceSupport: 0.7,
    priority: "medium",
    trackingStatus: "watching",
    trend: "up",
    valueScore: 75,
  },
];

describe("EventTable", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });

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

  it("offers a World Cup shortcut that filters sports events and updates the URL", async () => {
    render(<EventTable events={eventsWithSports} total={3} />);

    await waitFor(() => expect(screen.getByLabelText("按领域筛选")).toHaveValue("all"));

    await userEvent.click(screen.getByRole("button", { name: /世界杯/ }));

    expect(screen.getByText("Brazil World Cup semifinal")).toBeInTheDocument();
    expect(screen.queryByText("Federal Reserve rate cut")).not.toBeInTheDocument();
    expect(screen.getByLabelText("按领域筛选")).toHaveValue("sports_event");
    expect(screen.getByRole("button", { name: /世界杯/ })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(window.location.search).toContain("category=sports_event"));

    await userEvent.click(screen.getByRole("button", { name: /世界杯/ }));

    await waitFor(() => expect(window.location.search).not.toContain("category="));
    expect(screen.getByLabelText("按领域筛选")).toHaveValue("all");
  });
});
