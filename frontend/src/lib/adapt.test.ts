import { describe, expect, it } from "vitest";
import { adaptRecord, sparkSeries, trendOf } from "./adapt";
import type { EventRecord } from "./types";

describe("adaptRecord", () => {
  it("prefers user tracking fields over inferred defaults", () => {
    const record: EventRecord = {
      event_id: "evt-1",
      event_title: "Will rates fall?",
      event_title_zh: "利率会下降吗？",
      event_summary: "summary",
      probability: { baseline: 45, estimated: 58, change: 13 },
      credibility: { confidence: 0.7 },
      impact: { level: "LOW" },
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
    const view = adaptRecord({
      event_id: "evt-2",
      event_title: "Minimal event",
    });

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

  it.each([
    ["boe-rates", "No change in Bank of England's interest rates after July 2026 meeting?", "monetary"],
    ["trump-russia", "Will Donald Trump visit Russia in 2026?", "geopolitics_general"],
    ["ufc-tko", "Will Conor McGregor win by KO or TKO?", "sports_game"],
    ["boe-rates-zh", "\u82f1\u56fd\u592e\u884c\u5229\u7387\u4e0d\u53d8\uff1f", "monetary"],
    ["epstein-storage", "Epstein storage units raided in 2026?", "legal"],
    [
      "israel-litani",
      "Will Israeli forces withdraw from beyond the Litani River by December 31?",
      "geopolitics_general",
    ],
    [
      "lebron-cavaliers",
      "Will LeBron James play for the Cleveland Cavaliers in the 2026-27 season?",
      "sports_general",
    ],
    ["israel-airspace", "Israel closes its airspace by July 31?", "geopolitics_general"],
    ["saibari-shots", "Ismael Saibari: 1+ shots", "sports_game"],
    ["hype-hourly", "HYPE Up or Down - Hourly", "crypto"],
  ])("infers %s from title when prediction-market metadata is generic", (eventId, title, expected) => {
    const record = {
      event_id: eventId,
      event_title: title,
      source: {
        type: "prediction_market",
        platform: "Polymarket",
        category: "Prediction",
      },
      legacy_analysis: {},
    } as EventRecord & { legacy_analysis: Record<string, unknown> };

    const view = adaptRecord(record);

    expect(view.category).toBe(expected);
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
    expect(sparkSeries([{ estimated: 10 }, {}, { estimated: 0 }, { estimated: 35 }, { estimated: "bad" }])).toEqual([
      10,
      0,
      35,
    ]);
  });
});
