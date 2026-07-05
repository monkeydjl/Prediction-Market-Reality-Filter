import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DecisionsPage from "./page";
import { eventsApi, type DecisionReport, type FreshEdge } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    eventsApi: {
      ...actual.eventsApi,
      openDecisions: vi.fn(),
      freshEdges: vi.fn(),
    },
  };
});

const api = vi.mocked(eventsApi);

function decision(overrides: Partial<DecisionReport> = {}): DecisionReport {
  return {
    event_id: "event-1",
    event: { title: "Duplicate market event", summary: "summary" },
    probability: { estimated: 0.62, baseline: 0.5, change: 0.12, direction: "up" },
    market_view: { market_probability: 0.5, platform: "polymarket", liquidity: 1000, volume: 5000 },
    edge: { raw: 12, adjusted: 8, trust: 0.7 },
    diagnosis: {
      qualified: true,
      segment_n: 20,
      segment_min_samples: 10,
      segment_skill: 0.2,
      liquidity_factor: 0.8,
      reason: "Strong enough edge.",
    },
    confidence: { level: "medium", score: 0.7, confidence: 0.7 },
    recommendation: { decision: "act", action: "BUY_YES", calibration_status: "qualified" },
    risk: { level: "medium", flags: [] },
    category: "politics",
    status: "active",
    actionable_recommendation: null,
    ...overrides,
  };
}

function freshEdge(overrides: Partial<FreshEdge> = {}): FreshEdge {
  return {
    event_id: "event-1",
    event_title: "Duplicate market event",
    edge: {
      observations: 4,
      latest_edge: 8,
      first_edge: 2,
      peak_edge: 9,
      net_edge_change: 6,
      recent_edge_change: 1,
      age_hours: 2,
      freshness_band: "fresh",
      classification: "fresh",
    },
    ...overrides,
  };
}

describe("DecisionsPage", () => {
  beforeEach(() => {
    api.openDecisions.mockReset();
    api.freshEdges.mockReset();
  });

  it("does not render the fresh-edge section on the all decision filter", async () => {
    api.openDecisions.mockResolvedValue({ count: 1, decisions: [decision()] });
    api.freshEdges.mockResolvedValue({ count: 1, edges: [freshEdge()] });

    render(<DecisionsPage />);

    await waitFor(() => expect(api.freshEdges).toHaveBeenCalled());

    expect(screen.queryByText("?? Edge")).not.toBeInTheDocument();
    expect(screen.getAllByText("Duplicate market event")).toHaveLength(1);
  });

  it("paginates decision events and resets to page one when filter changes", async () => {
    const user = userEvent.setup();
    api.openDecisions.mockImplementation(async (decisionFilter, limit = 10, offset = 0) => ({
      count: offset === 0 ? 10 : 1,
      total: decisionFilter === "act" ? 3 : 11,
      limit,
      offset,
      decision_totals: { act: 3, provisional_act: 4, watch: 4 },
      decisions: offset === 0
        ? [decision({ event_id: "event-page-1", event: { title: "Decision page 1", summary: "summary" } })]
        : [decision({ event_id: "event-page-2", event: { title: "Decision page 2", summary: "summary" } })],
    }));
    api.freshEdges.mockResolvedValue({ count: 0, edges: [] });

    render(<DecisionsPage />);

    await screen.findByText("Decision page 1");
    expect(api.openDecisions).toHaveBeenCalledWith(undefined, 10, 0);
    expect(screen.getByText("\u7b2c 1 / 2 \u9875 \u00b7 \u5171 11 \u6761")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /\u4e0b\u4e00\u9875/ }));

    await waitFor(() => expect(api.openDecisions).toHaveBeenLastCalledWith(undefined, 10, 10));
    expect(await screen.findByText("Decision page 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /\u5efa\u8bae\u884c\u52a8/ }));

    await waitFor(() => expect(api.openDecisions).toHaveBeenLastCalledWith("act", 10, 0));
  });
});
