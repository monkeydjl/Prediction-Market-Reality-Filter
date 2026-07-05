import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecentPredictions } from "./recent-predictions";
import { eventsApi, type PredictionRecord } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    eventsApi: {
      ...actual.eventsApi,
      recentPredictions: vi.fn(),
    },
  };
});

const api = vi.mocked(eventsApi);

function prediction(index: number): PredictionRecord {
  return {
    id: `prediction-${index}`,
    event_id: `event-${index}`,
    event_title: `Prediction event ${index}`,
    ai_probability: 60,
    market_probability: 50,
    raw_edge: 10,
    created_at: "2026-07-05T00:00:00Z",
    status: "open",
  };
}

describe("RecentPredictions", () => {
  beforeEach(() => {
    api.recentPredictions.mockReset();
  });

  it("paginates recent predictions with 10 records per page", async () => {
    const user = userEvent.setup();
    api.recentPredictions.mockImplementation(async (_limit = 10, offset = 0) => ({
      predictions: offset === 0
        ? Array.from({ length: 10 }, (_, i) => prediction(i + 1))
        : [prediction(11)],
      count: offset === 0 ? 10 : 1,
      total: 11,
      limit: 10,
      offset,
    }));

    render(<RecentPredictions />);

    await waitFor(() => expect(api.recentPredictions).toHaveBeenCalledWith(10, 0));
    expect(await screen.findByText("Prediction event 1")).toBeInTheDocument();
    expect(screen.queryByText("Prediction event 11")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /\u4e0b\u4e00\u9875/ }));

    await waitFor(() => expect(api.recentPredictions).toHaveBeenLastCalledWith(10, 10));
    expect(await screen.findByText("Prediction event 11")).toBeInTheDocument();
    expect(screen.queryByText("Prediction event 1")).not.toBeInTheDocument();
  });
});
