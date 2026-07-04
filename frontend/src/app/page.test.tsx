import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DashboardPage from "./page";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  movers: vi.fn(),
  batchSparklines: vi.fn(),
  discoverStatus: vi.fn(),
  discover: vi.fn(),
  resetData: vi.fn(),
  translateAll: vi.fn(),
}));

vi.mock("@/components/app-nav", () => ({
  AppNav: () => <nav aria-label="app nav" />,
}));

vi.mock("@/components/dashboard/summary-bar", () => ({
  SummaryBar: () => <section aria-label="summary" />,
  summarize: () => ({ total: 0 }),
}));

vi.mock("@/components/dashboard/movers-board", () => ({
  MoversBoard: () => <section aria-label="movers" />,
}));

vi.mock("@/components/dashboard/event-table", () => ({
  EventTable: () => <section aria-label="events" />,
}));

vi.mock("@/components/dashboard/system-status", () => ({
  SystemStatus: () => <section aria-label="system" />,
}));

vi.mock("@/components/section-error-boundary", () => ({
  SectionErrorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    eventsApi: api,
  };
});

describe("DashboardPage batch translation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.list.mockResolvedValue({ events: [], total: 0, count: 0 });
    api.movers.mockResolvedValue({ movers: [] });
    api.batchSparklines.mockResolvedValue({ sparklines: {} });
    api.translateAll.mockResolvedValue({
      translated: 2,
      total: 5,
      message: "Translated 2 event titles",
    });
  });

  it("lets an operator batch translate missing event titles after confirmation", async () => {
    const user = userEvent.setup();
    render(<DashboardPage />);

    await user.click(screen.getByRole("button", { name: /批量翻译/ }));
    await user.click(screen.getByRole("button", { name: "确认翻译" }));

    await waitFor(() => {
      expect(api.translateAll).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText(/Translated 2 event titles/)).toBeInTheDocument();
  });
});
