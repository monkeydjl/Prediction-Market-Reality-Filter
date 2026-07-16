import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OfficialColumn, NewsColumn } from "./evidence-list";
import type { EventRecord, EvidenceItem } from "@/lib/types";

function makeItem(overrides: Partial<EvidenceItem> = {}): EvidenceItem {
  return {
    kind: "news",
    source: "reuters",
    title: "Markets rally on rate cut hopes",
    summary: "Stocks surged after the central bank signaled a possible cut.",
    url: "https://example.com/story",
    published: "2026-07-15T10:00:00Z",
    quality: 0.8,
    relevance: 0.6,
    ...overrides,
  };
}

const baseRecord: EventRecord = {
  event_id: "evt-1",
  event_title: "Will the policy pass?",
  event_summary: "",
  probability: { baseline: 50, estimated: 60, change: 10, direction: "up" },
  credibility: { score: 0.7, level: "medium", confidence: 0.5, news_quality: 0.6, evidence_strength: 0.7, source_count: 2 },
  impact: { score: 0.6, level: "medium", drivers: [] },
  risk: { level: "medium" },
  evidence: { direction: "supports", strength: 0.6, conflict: 0.2, freshness: 0.7, resolution_relevance: 0.5 },
  source: { type: "prediction_market" },
  value_score: 0.4,
  intelligence_report: {
    headline: "h",
    why_it_matters: "w",
    probability_assessment: "p",
    recommended_action: "r",
  },
};

describe("OfficialColumn", () => {
  it("renders the empty state when no official evidence is present", () => {
    render(<OfficialColumn record={{ ...baseRecord, evidence_items: [] }} />);
    expect(screen.getByText("暂无该来源证据")).toBeInTheDocument();
  });

  it("renders only official items and counts them", () => {
    const items: EvidenceItem[] = [
      makeItem({ kind: "official", source: "fed", title: "Fed statement" }),
      makeItem({ kind: "news", source: "reuters", title: "Reuters take" }),
      makeItem({ kind: "official", source: "treasury", title: "Treasury release" }),
    ];
    render(<OfficialColumn record={{ ...baseRecord, evidence_items: items }} />);

    expect(screen.getByText("Fed statement")).toBeInTheDocument();
    expect(screen.getByText("Treasury release")).toBeInTheDocument();
    // News item filtered out
    expect(screen.queryByText("Reuters take")).not.toBeInTheDocument();
    // Count badge shows number of official items
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});

describe("NewsColumn", () => {
  it("treats items without an explicit kind as news", () => {
    const items: EvidenceItem[] = [
      makeItem({ kind: "official", source: "fed", title: "Fed statement" }),
      makeItem({ source: "reuters", title: "Reuters take" }), // kind undefined → news
    ];
    render(<NewsColumn record={{ ...baseRecord, evidence_items: items }} />);

    expect(screen.queryByText("Fed statement")).not.toBeInTheDocument();
    expect(screen.getByText("Reuters take")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("prefers title_zh over title when present", () => {
    const items: EvidenceItem[] = [
      makeItem({ title: "English title", title_zh: "中文标题" }),
    ];
    render(<NewsColumn record={{ ...baseRecord, evidence_items: items }} />);
    expect(screen.getByText("中文标题")).toBeInTheDocument();
    expect(screen.queryByText("English title")).not.toBeInTheDocument();
  });
});
