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
});
