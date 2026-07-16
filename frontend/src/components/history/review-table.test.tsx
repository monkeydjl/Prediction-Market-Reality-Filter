import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReviewTable, toReview, type ResolvedReview } from "./review-table";
import type { EventRecord } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/csv", () => ({
  downloadCsv: vi.fn(),
}));

describe("toReview", () => {
  it("returns null when the event has no resolved outcome", () => {
    const record: Partial<EventRecord> = {
      event_id: "evt-1",
      event_title: "no outcome",
      outcome: null,
      calibration: null,
    };
    expect(toReview(record as EventRecord)).toBeNull();
  });

  it("maps a resolved record to a review and computes correctness against 50%", () => {
    const record: Partial<EventRecord> = {
      event_id: "evt-2",
      event_title: "Rate cut",
      event_title_zh: "降息",
      probability: { baseline: 40, estimated: 65, change: 25, direction: "up" },
      outcome: {
        status: "resolved",
        actual_outcome: 80,
        confidence: 1,
        resolved_at: "2026-07-15T00:00:00Z",
        source: "manual",
      },
      calibration: {
        brier_score: 0.12,
        skill_score: 0.2,
        grade: "GOOD",
        estimated_probability: 65,
        actual_outcome: 80,
        trajectory_observations: 1,
      },
    };
    const review = toReview(record as EventRecord) as ResolvedReview;
    expect(review).not.toBeNull();
    expect(review.title).toBe("降息");
    expect(review.predicted).toBe(65);
    expect(review.actual).toBe(80);
    expect(review.brier).toBe(0.12);
    expect(review.grade).toBe("GOOD");
    // predicted (65) >= 50 === actual (80) >= 50 → correct
    expect(review.correct).toBe(true);
  });

  it("marks a review as incorrect when predicted and actual straddle 50%", () => {
    const record: Partial<EventRecord> = {
      event_id: "evt-3",
      event_title: "Miss",
      outcome: {
        status: "resolved",
        actual_outcome: 20,
        confidence: 1,
        resolved_at: "2026-07-15T00:00:00Z",
        source: "manual",
      },
      calibration: null,
      probability: { baseline: 60, estimated: 70, change: 10, direction: "up" },
    };
    const review = toReview(record as EventRecord) as ResolvedReview;
    // predicted (70) >= 50 === true; actual (20) >= 50 === false → mismatch → incorrect
    expect(review.correct).toBe(false);
  });
});

describe("ReviewTable", () => {
  it("renders the empty state when there are no reviews", () => {
    render(<ReviewTable reviews={[]} />);
    expect(screen.getByText(/暂无已结算事件/)).toBeInTheDocument();
    // Export button is disabled when there are no reviews
    expect(screen.getByRole("button", { name: /导出/ })).toBeDisabled();
  });

  it("renders a row per review with title, predicted %, actual label, and outcome tag", () => {
    const reviews: ResolvedReview[] = [
      {
        id: "evt-a",
        title: "Pass",
        predicted: 70,
        actual: 80,
        brier: 0.12,
        grade: "GOOD",
        correct: true,
        resolvedAt: "2026-07-15T00:00:00Z",
      },
      {
        id: "evt-b",
        title: "Miss",
        predicted: 70,
        actual: 20,
        brier: 0.4,
        grade: "POOR",
        correct: false,
        resolvedAt: "2026-07-16T00:00:00Z",
      },
    ];
    render(<ReviewTable reviews={reviews} />);

    expect(screen.getByText("Pass")).toBeInTheDocument();
    expect(screen.getByText("Miss")).toBeInTheDocument();
    // Correctness tags
    expect(screen.getByText("判断正确")).toBeInTheDocument();
    expect(screen.getByText("判断错误")).toBeInTheDocument();
    // Actual outcome label depends on >=50 threshold
    expect(screen.getByText("发生")).toBeInTheDocument();
    expect(screen.getByText("未发生")).toBeInTheDocument();
    // Header shows review count
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});
