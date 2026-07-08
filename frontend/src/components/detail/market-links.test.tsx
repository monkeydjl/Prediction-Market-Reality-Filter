import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarketPanel } from "./market-links";
import type { EventRecord } from "@/lib/types";

function record(): EventRecord {
  return {
    event_id: "evt-1",
    event_title: "Will Bitcoin reach $100,000 in 2026?",
    event_summary: "",
    probability: {
      baseline: 50,
      estimated: 55,
      change: 5,
      direction: "up",
    },
    source: {
      type: "prediction_market",
      platform: "Manifold",
      baseline_probability: 52,
      volume: 1000,
      liquidity: 500,
      url: "https://manifold.markets/old-market",
    },
    credibility: {
      score: 60,
      level: "medium",
      confidence: 0.6,
      news_quality: 0.6,
      evidence_strength: 0.6,
      source_count: 2,
    },
    impact: { score: 50, level: "medium", drivers: [] },
    risk: { level: "LOW", flags: [] },
    evidence: {
      direction: "positive",
      strength: 0.5,
      conflict: 0,
      freshness: 0.5,
      resolution_relevance: 0.5,
    },
    value_score: 10,
    intelligence_report: {
      headline: "",
      why_it_matters: "",
      probability_assessment: "",
      recommended_action: "",
    },
  };
}

describe("MarketPanel", () => {
  it("renders active and planned platform links with on-chain labels but not Manifold", () => {
    render(<MarketPanel record={record()} />);

    expect(screen.getByRole("link", { name: /Polymarket/i })).toHaveAttribute(
      "href",
      expect.stringContaining("polymarket.com"),
    );
    expect(screen.getByRole("link", { name: /Kalshi/i })).toHaveAttribute(
      "href",
      expect.stringContaining("kalshi.com"),
    );
    expect(screen.getByRole("link", { name: /Opinion/i })).toHaveAttribute(
      "href",
      "https://app.opinion.trade/trending",
    );
    expect(screen.getByRole("link", { name: /Limitless/i })).toHaveAttribute(
      "href",
      "https://limitless.exchange/",
    );
    expect(screen.getByRole("link", { name: /Predict\.fun/i })).toHaveAttribute(
      "href",
      "https://predict.fun/",
    );
    expect(screen.getByRole("link", { name: /Probable/i })).toHaveAttribute(
      "href",
      "https://probable.finance/",
    );

    expect(screen.getAllByText("BNB Chain")).toHaveLength(3);
    expect(screen.getByText("Base")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Manifold/i })).toBeNull();
  });

  it("still displays historical Manifold platform text", () => {
    render(<MarketPanel record={record()} />);

    expect(screen.getByText("Manifold")).toBeInTheDocument();
  });
});
