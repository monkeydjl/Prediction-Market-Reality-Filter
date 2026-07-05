import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HistoryPage from "./page";
import { eventsApi } from "@/lib/api";

vi.mock("@/components/history/accuracy-summary", () => ({
  AccuracySummary: () => <section data-testid="accuracy-summary">accuracy summary</section>,
}));

vi.mock("@/components/history/prediction-calibration", () => ({
  PredictionCalibrationCard: () => <section data-testid="prediction-calibration">prediction calibration</section>,
}));

vi.mock("@/components/history/category-accuracy", () => ({
  CategoryAccuracy: () => <section data-testid="category-accuracy">category accuracy</section>,
  toCategoryData: () => [],
}));

vi.mock("@/components/history/pending-links", () => ({
  PendingLinks: () => <section data-testid="pending-links">pending links</section>,
}));


vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    eventsApi: {
      ...actual.eventsApi,
      calibration: vi.fn(),
      predictionCalibration: vi.fn(),
      list: vi.fn(),
      resolveAuto: vi.fn(),
      recentPredictions: vi.fn(),
    },
  };
});

const api = vi.mocked(eventsApi);

function resolvedEntry(index: number) {
  return {
    record: {
      event_id: `resolved-${index}`,
      event_title: `Resolved event ${index}`,
      probability: { estimated: 60 },
      calibration: { estimated_probability: 60, brier_score: 0.16, grade: "good" },
      outcome: { actual_outcome: 100, resolved_at: "2026-07-05T00:00:00Z" },
    },
  };
}

function openEntry(index: number) {
  return {
    record: {
      event_id: `open-${index}`,
      event_title: `Open event ${index}`,
      probability: { estimated: 40 },
    },
  };
}

describe("HistoryPage", () => {
  beforeEach(() => {
    api.calibration.mockReset();
    api.predictionCalibration.mockReset();
    api.list.mockReset();
    api.resolveAuto.mockReset();
    api.recentPredictions.mockReset();

    api.calibration.mockResolvedValue({
      overall: { brier_score: 0.2, skill_score: 0.1, grade: "ok", n: 1 },
      by_source: {},
      by_base_rate_category: {},
    });
    api.predictionCalibration.mockResolvedValue({
      n: 1,
      brier_score: 0.2,
      grade: "ok",
      mean_raw_edge: null,
      realized_edge: null,
      directional_hit_rate: null,
      segment_min_samples: null,
      by_category: {},
      segments: {},
    });
    api.list.mockResolvedValue({
      events: [resolvedEntry(1)],
      count: 1,
      total: 1,
      limit: 10,
      offset: 0,
    });
    api.recentPredictions.mockResolvedValue({ predictions: [] });
  });

  it("switches between recent predictions and resolved reviews instead of rendering both", async () => {
    const user = userEvent.setup();

    render(<HistoryPage />);

    await waitFor(() => expect(api.list).toHaveBeenCalled());
    expect(await screen.findByRole("heading", { name: /已结算判断/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /最近预测记录/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /最近预测记录/ }));
    expect(await screen.findByRole("heading", { name: /最近预测记录/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /已结算判断/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /已结算判断/ }));
    expect(await screen.findByRole("heading", { name: /已结算判断/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /最近预测记录/ })).not.toBeInTheDocument();
  });

  it("paginates resolved reviews with 10 records per page", async () => {
    const user = userEvent.setup();
    api.list.mockImplementation(async (_limit = 10, offset = 0) => ({
      events: offset === 0
        ? Array.from({ length: 10 }, (_, i) => resolvedEntry(i + 1))
        : [resolvedEntry(11)],
      count: offset === 0 ? 10 : 1,
      total: 11,
      limit: 10,
      offset,
    }));

    render(<HistoryPage />);

    await waitFor(() => expect(api.list).toHaveBeenCalledWith(10, 0, { resolved_only: true, exclude_expired: false }));
    expect(await screen.findByText("Resolved event 1")).toBeInTheDocument();
    expect(screen.queryByText("Resolved event 11")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /\u4e0b\u4e00\u9875/ }));

    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith(10, 10, { resolved_only: true, exclude_expired: false }));
    expect(await screen.findByText("Resolved event 11")).toBeInTheDocument();
    expect(screen.queryByText("Resolved event 1")).not.toBeInTheDocument();
  });

  it("requests resolved reviews from the server before paginating", async () => {
    api.list.mockImplementation(async (_limit = 10, offset = 0, filters = {}) => {
      if (filters.resolved_only) {
        return {
          events: Array.from({ length: 10 }, (_, i) => resolvedEntry(offset + i + 1)),
          count: 10,
          total: 24,
          limit: 10,
          offset,
        };
      }
      return {
        events: [
          ...Array.from({ length: 6 }, (_, i) => resolvedEntry(i + 1)),
          ...Array.from({ length: 4 }, (_, i) => openEntry(i + 1)),
        ],
        count: 10,
        total: 24,
        limit: 10,
        offset,
      };
    });

    render(<HistoryPage />);

    await waitFor(() => expect(api.list).toHaveBeenCalledWith(10, 0, { resolved_only: true, exclude_expired: false }));
    expect(await screen.findByText("Resolved event 10")).toBeInTheDocument();
    expect(screen.queryByText("Open event 1")).not.toBeInTheDocument();
  });
});
