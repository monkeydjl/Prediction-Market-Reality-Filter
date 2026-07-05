import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "./page";
import { clearDashboardCache } from "@/lib/dashboard-cache";

const apiMocks = vi.hoisted(() => ({
  list: vi.fn(),
  movers: vi.fn(),
  batchSparklines: vi.fn(),
  resolveExpired: vi.fn(),
  discoverStatus: vi.fn(),
  discover: vi.fn(),
  resetData: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  eventsApi: apiMocks,
}));

vi.mock("@/components/dashboard/summary-bar", () => ({
  summarize: (events: Array<unknown>) => ({ total: events.length }),
  SummaryBar: ({ summary }: { summary: { total: number } }) => (
    <div data-testid="summary">{summary.total}</div>
  ),
}));

vi.mock("@/components/dashboard/movers-board", () => ({
  MoversBoard: () => <div data-testid="movers-board" />,
}));

vi.mock("@/components/dashboard/event-table", () => ({
  EventTable: ({ events }: { events: Array<{ id: string; title: string }> }) => (
    <div data-testid="event-table">
      {events.map((event) => (
        <div key={event.id}>{event.title}</div>
      ))}
    </div>
  ),
}));

vi.mock("@/components/dashboard/system-status", () => ({
  SystemStatus: () => <div data-testid="system-status" />,
}));

const trackedEvent = {
  event_id: "evt-1",
  first_seen: "",
  last_updated: "",
  record: {
    event_id: "evt-1",
    event_title: "Cached Event",
    event_summary: "",
    probability: {
      estimated: 60,
      baseline: 50,
      change: 10,
      direction: "up",
    },
    credibility: { confidence: 0.8 },
    impact: { level: "HIGH" },
    source: { type: "general" },
    value_score: 90,
  },
};

describe("DashboardPage cache", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
    clearDashboardCache();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.batchSparklines.mockResolvedValue({ sparklines: {} });
    apiMocks.discoverStatus.mockResolvedValue({ phase: "idle", message: "等待开始" });
  });

  afterEach(() => {
    clearDashboardCache();
  });

  it("shows cached dashboard data immediately when returning before refresh finishes", async () => {
    apiMocks.list.mockResolvedValueOnce({ events: [trackedEvent], total: 1 });
    apiMocks.movers.mockResolvedValueOnce({ movers: [] });

    const first = render(<DashboardPage />);

    expect(await screen.findByText("Cached Event")).toBeInTheDocument();
    first.unmount();

    apiMocks.list.mockReturnValueOnce(new Promise(() => undefined));
    apiMocks.movers.mockReturnValueOnce(new Promise(() => undefined));

    render(<DashboardPage />);
    await waitFor(() => expect(apiMocks.list).toHaveBeenCalledTimes(2));

    await waitFor(() => {
      expect(screen.getByText("Cached Event")).toBeInTheDocument();
    }, { timeout: 250 });
  });

  it("does not abort event discovery when navigating away", async () => {
    apiMocks.list.mockResolvedValue({ events: [], total: 0 });
    apiMocks.movers.mockResolvedValue({ movers: [] });
    apiMocks.discover.mockReturnValue(new Promise(() => undefined));

    const view = render(<DashboardPage />);

    await userEvent.click(screen.getByRole("button", { name: "发现新事件" }));
    const signal = apiMocks.discover.mock.calls[0]?.[2] as AbortSignal | undefined;

    view.unmount();

    expect(signal?.aborted).not.toBe(true);
  });

  it("resumes the discovery progress banner when returning during a backend run", async () => {
    apiMocks.list.mockResolvedValue({ events: [], total: 0 });
    apiMocks.movers.mockResolvedValue({ movers: [] });
    apiMocks.discoverStatus.mockResolvedValue({
      phase: "analyzing",
      message: "分析中 3/10…",
      analyzed: 3,
      total_to_analyze: 10,
      elapsed_ms: 12_000,
    });

    render(<DashboardPage />);

    expect(await screen.findByText("分析中 3/10…")).toBeInTheDocument();
    expect(screen.getByText(/已用 12 秒/)).toBeInTheDocument();
  });

  it("labels expired-market cleanup as archiving instead of settlement", async () => {
    apiMocks.list.mockResolvedValue({ events: [], total: 0 });
    apiMocks.movers.mockResolvedValue({ movers: [] });
    apiMocks.resolveExpired.mockResolvedValue({
      total: 1,
      resolved: 0,
      archived: 1,
      parsed_dates: 0,
      message: "Archived 1 expired events without resolving outcomes",
    });

    render(<DashboardPage />);

    await userEvent.click(await screen.findByRole("button", { name: "归档过期" }));

    await waitFor(() => expect(apiMocks.resolveExpired).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "结算过期" })).not.toBeInTheDocument();
  });
});
