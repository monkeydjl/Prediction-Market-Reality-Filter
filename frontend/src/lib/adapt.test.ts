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
});

describe("trendOf", () => {
  it("uses a small dead band around flat moves", () => {
    expect(trendOf(0.5)).toBe("flat");
    expect(trendOf(0.6)).toBe("up");
    expect(trendOf(-0.6)).toBe("down");
  });
});

describe("sparkSeries", () => {
  it("drops missing and zero estimates", () => {
    expect(sparkSeries([{ estimated: 10 }, {}, { estimated: 0 }, { estimated: 35 }])).toEqual([10, 35]);
  });
});
