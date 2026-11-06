import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { EventView } from "@/lib/adapt";
import { SignalSummary } from "./signal-summary";

const baseEvent: EventView = {
  id: "evt-1",
  title: "Will the market move?",
  description: "",
  category: "general",
  currentProbability: 52,
  baselineProbability: 50,
  delta: 2,
  direction: "up",
  evidenceSupport: 0.5,
  priority: "medium",
  trackingStatus: "watching",
  trend: "up",
  valueScore: 0,
};

describe("SignalSummary", () => {
  it("labels watch verdicts as event tracking guidance, not trading action guidance", () => {
    render(<SignalSummary event={baseEvent} />);

    expect(screen.getByText("事件跟踪概要")).toBeInTheDocument();
    expect(screen.getByText("继续观察事件进展")).toBeInTheDocument();
    expect(screen.getByText("此处评估是否继续跟踪事件，不代表交易行动建议。")).toBeInTheDocument();
    expect(screen.queryByText("判断概要")).not.toBeInTheDocument();
    expect(screen.queryByText("保持观察")).not.toBeInTheDocument();
  });

  // Locks the direction-color convention 涨红跌绿 (CN market convention) on
  // the 概率变化 metric: it is the same movement quantity (and the same ±0.5
  // deadband) as DeltaPill, so rising = red (--up), falling = green (--down).
  // Valence --pos/--neg stays on the quality metrics only.
  it("colors 概率变化 rising red and falling green per the direction convention", () => {
    const { rerender } = render(
      <SignalSummary event={{ ...baseEvent, delta: 3.2 }} />,
    );
    const rising = screen.getByText("+3.2pt");
    expect(rising).toHaveClass("text-up");
    expect(rising).not.toHaveClass("text-pos");
    expect(rising).not.toHaveClass("text-neg");

    rerender(<SignalSummary event={{ ...baseEvent, delta: -3.2 }} />);
    const falling = screen.getByText("-3.2pt");
    expect(falling).toHaveClass("text-down");
    expect(falling).not.toHaveClass("text-neg");
    expect(falling).not.toHaveClass("text-pos");

    rerender(<SignalSummary event={{ ...baseEvent, delta: 0.3 }} />);
    const flat = screen.getByText("+0.3pt");
    expect(flat).toHaveClass("text-muted-foreground");
    expect(flat).not.toHaveClass("text-up");
    expect(flat).not.toHaveClass("text-down");

    // Quality metric keeps valence: high evidence support = green (pos).
    rerender(<SignalSummary event={{ ...baseEvent, evidenceSupport: 0.7 }} />);
    const support = screen.getByText("70%");
    expect(support).toHaveClass("text-pos");
    expect(support).not.toHaveClass("text-up");
  });
});
