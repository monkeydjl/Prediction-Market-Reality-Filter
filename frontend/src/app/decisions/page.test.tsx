import { render, screen, waitFor } from "@testing-library/react";
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

    expect(screen.queryByText("新鲜 Edge")).not.toBeInTheDocument();
    expect(screen.getAllByText("Duplicate market event")).toHaveLength(1);
  });
});
