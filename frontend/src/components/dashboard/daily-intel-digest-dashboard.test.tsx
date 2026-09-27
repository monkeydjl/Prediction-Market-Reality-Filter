import { act, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DailyIntelDigestDashboard, REFRESH_MS } from "./daily-intel-digest-dashboard";
import { eventsApi, type DailyDigestResponse } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  eventsApi: {
    digest: vi.fn(),
  },
}));

const fullPayload: DailyDigestResponse = {
  date: "2026-09-25",
  generated_at: "2026-09-25T23:00:00+00:00",
  count: 1,
  headline: {
    event_id: "ev-1",
    event_title: "事件一",
    movement: "rising",
    day_net: 4.2,
    close: 61.1,
  },
  movers: [
    {
      event_id: "ev-1",
      event_title: "事件一",
      movement: "rising",
      day_net: 4.2,
      open: 56.9,
      close: 61.1,
      open_source: "previous_close",
      day_observations: 3,
      all_time_net_change: 11.2,
      close_ts: "2026-09-25T22:30:00+00:00",
    },
  ],
  unchanged: {
    quiet_event_ids: ["ev-2"],
    new_event_ids: ["ev-3"],
  },
  empty: false,
};

describe("DailyIntelDigestDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(eventsApi.digest).mockResolvedValue(fullPayload);
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
  });

  it("renders headline and movers after load", async () => {
    vi.useFakeTimers();
    try {
      render(<DailyIntelDigestDashboard />);
      await act(async () => {
        await vi.runOnlyPendingTimersAsync();
      });

      expect(within(screen.getByTestId("digest-headline")).getByText("事件一")).toBeInTheDocument();
      expect(screen.getByTestId("digest-movers")).toBeInTheDocument();
      // Overview bar shows the digest day.
      expect(screen.getByText("2026-09-25")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders the empty payload message", async () => {
    vi.mocked(eventsApi.digest).mockResolvedValue({
      date: "2026-09-25",
      generated_at: "2026-09-25T23:00:00+00:00",
      count: 0,
      headline: null,
      movers: [],
      unchanged: { quiet_event_ids: [], new_event_ids: [] },
      empty: true,
    });

    vi.useFakeTimers();
    try {
      render(<DailyIntelDigestDashboard />);
      await act(async () => {
        await vi.runOnlyPendingTimersAsync();
      });

      expect(screen.getByText(/今日还没有产生任何概率快照/)).toBeInTheDocument();
      expect(screen.queryByTestId("digest-movers")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the quiet summary, not the empty banner, on an all-quiet day", async () => {
    // "empty" means nothing digestable at all. A day summarised by quiet
    // events must render that summary and never sit next to an empty-state
    // banner that contradicts it.
    vi.mocked(eventsApi.digest).mockResolvedValue({
      date: "2026-09-25",
      generated_at: "2026-09-25T23:00:00+00:00",
      count: 0,
      headline: null,
      movers: [],
      unchanged: { quiet_event_ids: ["ev-2", "ev-4"], new_event_ids: [] },
      empty: false,
    });

    vi.useFakeTimers();
    try {
      render(<DailyIntelDigestDashboard />);
      await act(async () => {
        await vi.runOnlyPendingTimersAsync();
      });

      expect(screen.getByText(/安静日/)).toBeInTheDocument();
      expect(screen.queryByText(/今日还没有产生任何概率快照/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not auto-refresh while the tab is hidden", async () => {
    vi.useFakeTimers();
    try {
      render(<DailyIntelDigestDashboard />);
      await act(async () => {
        await vi.runOnlyPendingTimersAsync();
      });

      vi.clearAllMocks();
      Object.defineProperty(document, "hidden", {
        configurable: true,
        value: true,
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(REFRESH_MS);
      });

      expect(eventsApi.digest).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does auto-refresh on the interval while the tab is visible", async () => {
    // Guards the interval itself: with REFRESH_MS exported and used above,
    // a regression that widened the interval past the advance would leave the
    // hidden-tab case passing vacuously.
    vi.useFakeTimers();
    try {
      render(<DailyIntelDigestDashboard />);
      await act(async () => {
        await vi.runOnlyPendingTimersAsync();
      });

      vi.clearAllMocks();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(REFRESH_MS);
      });

      expect(eventsApi.digest).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
