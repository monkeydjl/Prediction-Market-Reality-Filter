import { describe, expect, it } from "vitest";
import { adaptEntry, adaptRecord, sparkSeries, trendOf } from "./adapt";
import type { EventAuditSnapshot, EventRecord } from "./types";

describe("adaptRecord", () => {
  it("prefers user tracking fields over inferred defaults", () => {
    const record: EventRecord = {
      event_id: "evt-1",
      event_title: "Will rates fall?",
      event_title_zh: "利率会下降吗？",
      event_summary: "summary",
      probability: { baseline: 45, estimated: 58, change: 13, direction: "up" },
      credibility: {
        score: 70, level: "medium", confidence: 0.7,
        news_quality: 0.8, evidence_strength: 0.6, source_count: 3,
      },
      impact: { score: 30, level: "LOW", drivers: [] },
      risk: { level: "low" },
      evidence: {
        direction: "support", strength: 0.6,
        conflict: 0, freshness: 0.8, resolution_relevance: 0.7,
      },
      source: {},
      intelligence_report: {
        headline: "",
        why_it_matters: "",
        probability_assessment: "",
        recommended_action: "",
      },
      tracking: { status: "tracking", priority: "high" },
      value_score: 42,
    };

    const view = adaptRecord(record);

    expect(view.title).toBe("利率会下降吗？");
    expect(view.currentProbability).toBe(58);
    expect(view.baselineProbability).toBe(45);
    expect(view.priority).toBe("high");
    expect(view.trackingStatus).toBe("tracking");
    expect(view.trend).toBe("up");
  });

  it("falls back safely when optional backend fields are missing", () => {
    // Deliberately partial: the point of this case is a record that omits every
    // optional field, so it is cast rather than filled in.
    const view = adaptRecord({
      event_id: "evt-2",
      event_title: "Minimal event",
    } as unknown as EventRecord);

    expect(view.currentProbability).toBe(0);
    expect(view.priority).toBe("medium");
    expect(view.trackingStatus).toBe("watching");
    expect(view.category).toBe("general");
  });

  it("keeps sports events in the World Cup dashboard category", () => {
    const record = {
      event_id: "evt-3",
      event_title: "Will Brazil reach the World Cup semifinals?",
      source: { type: "sports_event", platform: "world_cup_2026" },
      legacy_analysis: { base_rate_category: "geopolitics" },
    } as EventRecord & { legacy_analysis: { base_rate_category: string } };

    const view = adaptRecord(record);

    expect(view.category).toBe("sports_event");
  });

  it("falls back from unknown base-rate category to source event type", () => {
    const record = {
      event_id: "evt-4",
      event_title: "Will Congress pass the budget bill?",
      source: { type: "open_web", platform: "news", event_type: "policy" },
      legacy_analysis: { base_rate_category: "unknown" },
    } as EventRecord & { legacy_analysis: { base_rate_category: string } };

    const view = adaptRecord(record);

    expect(view.category).toBe("policy");
  });

  it("skips generic legacy Prediction category when a precise event type exists", () => {
    const record = {
      event_id: "evt-legacy-generic",
      event_title: "Will Congress pass the budget bill?",
      source: { type: "prediction_market", platform: "Polymarket", event_type: "policy_general" },
      legacy_analysis: { base_rate_category: "Prediction" },
    } as EventRecord & { legacy_analysis: { base_rate_category: string } };

    const view = adaptRecord(record);

    expect(view.category).toBe("policy_general");
  });

  it("uses source category before generic source type", () => {
    const record = {
      event_id: "evt-5",
      event_title: "Will a player win the Golden Boot?",
      source: {
        type: "prediction_market",
        platform: "Polymarket",
        category: "player_awards",
      },
      legacy_analysis: {},
    } as EventRecord & { legacy_analysis: Record<string, unknown> };

    const view = adaptRecord(record);

    expect(view.category).toBe("player_awards");
  });

  it("skips generic Prediction source categories when a precise event type exists", () => {
    const record = {
      event_id: "evt-6",
      event_title: "Will heavy rain hit the city tomorrow?",
      source: {
        type: "prediction_market",
        platform: "Polymarket",
        category: "Prediction",
        event_type: "weather_event",
      },
      legacy_analysis: {},
    } as EventRecord & { legacy_analysis: Record<string, unknown> };

    const view = adaptRecord(record);

    expect(view.category).toBe("weather_event");
  });

  it("falls back to general when only prediction-market source fields exist", () => {
    const record = {
      event_id: "evt-7",
      event_title: "Will a generic market resolve yes?",
      source: {
        type: "prediction_market",
        platform: "Polymarket",
        category: "Prediction",
      },
      legacy_analysis: {},
    } as EventRecord & { legacy_analysis: Record<string, unknown> };

    const view = adaptRecord(record);

    expect(view.category).toBe("general");
  });

  it("uses the backend-derived entry category as the single source of truth", () => {
    const view = adaptEntry({
      event_id: "evt-entry-category",
      category: "monetary",
      record: {
        event_id: "evt-entry-category",
        event_title: "A title that should not be reclassified in the frontend",
        source: {
          type: "prediction_market",
          platform: "Polymarket",
          category: "Prediction",
        },
        legacy_analysis: {},
      } as EventRecord & { legacy_analysis: Record<string, unknown> },
    });

    expect(view.category).toBe("monetary");
  });

  it("does not infer source-of-truth categories from event text in the frontend", () => {
    const record = {
      event_id: "boe-rates",
      event_title: "No change in Bank of England's interest rates after July 2026 meeting?",
      source: {
        type: "prediction_market",
        platform: "Polymarket",
        category: "Prediction",
      },
      legacy_analysis: {},
    } as EventRecord & { legacy_analysis: Record<string, unknown> };

    const view = adaptRecord(record);

    expect(view.category).toBe("general");
  });

  it("does not use Limitless source platform as a domain category", () => {
    const record = {
      event_id: "evt-limitless",
      event_title: "Will a generic market resolve yes?",
      source: {
        type: "prediction_market",
        platform: "Limitless",
        category: "Prediction",
      },
      legacy_analysis: {},
    } as EventRecord & { legacy_analysis: Record<string, unknown> };

    const view = adaptRecord(record);

    expect(view.category).toBe("general");
  });
});

describe("trendOf", () => {
  it("uses a small dead band around flat moves", () => {
    expect(trendOf(0.5)).toBe("flat");
    expect(trendOf(0.6)).toBe("up");
    expect(trendOf(-0.6)).toBe("down");
  });
});

describe("sparkSeries", () => {
  it("keeps real zero estimates and drops invalid values", () => {
    // The inputs are invalid on purpose ({} has no estimate, "bad" is a string,
    // and the last entry has none at all): the cast is the test's whole subject.
    const malformed = [
      { estimated: 10 },
      {},
      { estimated: 0 },
      { estimated: 35 },
      { estimated: "bad" },
    ] as unknown as EventAuditSnapshot[];
    expect(sparkSeries(malformed)).toEqual([10, 0, 35]);
  });
});
