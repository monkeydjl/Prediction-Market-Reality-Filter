import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { OpenDecisionsList } from "./OpenDecisionsList";
import type { RecommendationList } from "@/lib/sport-recommendations-api";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const apiMocks = vi.hoisted(() => ({ fetchOpenDecisions: vi.fn() }));
vi.mock("@/lib/sport-recommendations-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sport-recommendations-api")>()),
  fetchOpenDecisions: apiMocks.fetchOpenDecisions,
}));

const mockData: RecommendationList = {
  items: [
    {
      match_id: "m1",
      mapped_outcome: "home_win",
      direction: "YES",
      decision: "act",
      confidence: "high",
      risk_level: "low",
      edge_pct: 7.2,
      raw_edge_pct: 10.0,
      trust: 0.72,
      liquidity_factor: 1.0,
      stale: false,
      suggested_allocation_pct: 2.0,
      calibration_status: "calibrated",
      rationale: "模型看好主胜",
      engine_name: "BasketballEngine",
      competition: "nba",
      prediction_timestamp: "2026-07-16T10:00:00Z",
      model_prob: 0.65,
      market_prob: 0.55,
      sources_count: 1,
      captured_at: "2026-07-16T10:00:00Z",
    },
  ],
  total: 1,
};

describe("OpenDecisionsList", () => {
  it("renders list after load", async () => {
    apiMocks.fetchOpenDecisions.mockResolvedValue(mockData);
    render(<OpenDecisionsList />);
    await waitFor(() =>
      expect(screen.getByTestId("open-decisions-list")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("rec-card-m1")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    apiMocks.fetchOpenDecisions.mockResolvedValue({ items: [], total: 0 });
    render(<OpenDecisionsList />);
    await waitFor(() => expect(screen.getByTestId("empty")).toBeInTheDocument());
  });

  it("renders error state", async () => {
    apiMocks.fetchOpenDecisions.mockRejectedValue(new Error("boom"));
    render(<OpenDecisionsList />);
    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
  });

  it("renders filter buttons", async () => {
    apiMocks.fetchOpenDecisions.mockResolvedValue(mockData);
    render(<OpenDecisionsList />);
    await waitFor(() =>
      expect(screen.getByTestId("open-decisions-list")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("filter-all")).toBeInTheDocument();
    expect(screen.getByTestId("filter-act")).toBeInTheDocument();
  });
});
