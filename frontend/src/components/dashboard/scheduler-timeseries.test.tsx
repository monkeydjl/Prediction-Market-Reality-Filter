import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SchedulerTimeseries } from "./scheduler-timeseries";
import type { SchedulerTimeseriesPoint } from "@/lib/api";

describe("SchedulerTimeseries", () => {
  it("renders the empty-state message when points is empty", () => {
    render(<SchedulerTimeseries points={[]} />);
    expect(screen.getByText(/调度时间线 — 无近期运行/)).toBeInTheDocument();
  });

  it("renders one row per point with job name, status, and duration", () => {
    const points: SchedulerTimeseriesPoint[] = [
      {
        job_name: "settle_events",
        status: "success",
        started_at: "2026-07-17T00:00:00Z",
        finished_at: "2026-07-17T00:00:05Z",
        duration_ms: 5000,
      },
      {
        job_name: "refresh_markets",
        status: "failed",
        started_at: "2026-07-17T00:05:00Z",
        finished_at: null,
        duration_ms: null,
      },
    ];
    render(<SchedulerTimeseries points={points} />);

    expect(screen.getByText("调度时间线")).toBeInTheDocument();
    expect(screen.getByText("settle_events")).toBeInTheDocument();
    expect(screen.getByText("refresh_markets")).toBeInTheDocument();
    expect(screen.getByText("success")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    // duration for first point
    expect(screen.getByText("5000ms")).toBeInTheDocument();
    // null duration renders "—"
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("caps the rendered rows at the first 100 points", () => {
    const points: SchedulerTimeseriesPoint[] = Array.from({ length: 5 }, (_, i) => ({
      job_name: `job_${i}`,
      status: "success",
      started_at: null,
      finished_at: null,
      duration_ms: null,
    }));
    render(<SchedulerTimeseries points={points} />);
    const rows = screen.getAllByRole("row");
    // 1 header + 5 data rows (well under the 100-row cap)
    expect(rows).toHaveLength(6);
    expect(screen.getByText("job_0")).toBeInTheDocument();
    expect(screen.getByText("job_4")).toBeInTheDocument();
  });
});
