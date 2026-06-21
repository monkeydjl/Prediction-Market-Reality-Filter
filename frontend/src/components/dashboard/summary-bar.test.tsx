import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SummaryBar, summarize } from "./summary-bar";

describe("summarize", () => {
  it("counts movement and tracking states", () => {
    expect(
      summarize([
        { delta: 2, valueScore: 10, trackingStatus: "tracking" },
        { delta: -3, valueScore: 20, trackingStatus: "archived" },
        { delta: 0.1, valueScore: 30, trackingStatus: "watching" },
      ]),
    ).toEqual({
      total: 3,
      rising: 1,
      falling: 1,
      avgValue: 20,
      tracking: 1,
      archived: 1,
    });
  });
});

describe("SummaryBar", () => {
  it("renders the key dashboard totals", () => {
    render(
      <SummaryBar
        summary={{ total: 5, rising: 2, falling: 1, avgValue: 42.5, tracking: 3, archived: 1 }}
      />,
    );

    expect(screen.getByText("在库事件")).toBeInTheDocument();
    expect(screen.getByText("平均情报价值")).toBeInTheDocument();
    expect(screen.getByText("42.5")).toBeInTheDocument();
  });
});
