import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EdgesPage from "./page";
import { eventsApi, type FreshEdge } from "@/lib/api";

vi.mock("next/link", () => ({
  default: ({ href, children, className }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => <a href={href} className={className}>{children}</a>,
}));

vi.mock("@/components/edges/edge-timeline-chart", () => ({
  EdgeTimelineChart: () => <div aria-hidden="true" />,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    eventsApi: {
      ...actual.eventsApi,
      edgeMonitor: vi.fn(),
    },
  };
});

const api = vi.mocked(eventsApi);

function edge(index: number, classification = "fresh"): FreshEdge {
  return {
    event_id: `edge-${index}`,
    event_title: `Edge event ${index}`,
    edge: {
      observations: 4,
      latest_edge: 8,
      first_edge: 2,
      peak_edge: 9,
      net_edge_change: 6,
      recent_edge_change: 1,
      age_hours: 2,
      freshness_band: "fresh",
      classification,
    },
    series: [
      { timestamp: "2026-07-05T00:00:00Z", estimated: 50, baseline: 45, edge: 5 },
      { timestamp: "2026-07-05T01:00:00Z", estimated: 60, baseline: 52, edge: 8 },
    ],
  };
}

describe("EdgesPage", () => {
  beforeEach(() => {
    api.edgeMonitor.mockReset();
  });

  it("paginates monitored edge events and reloads from page one when class changes", async () => {
    const user = userEvent.setup();
    api.edgeMonitor.mockImplementation(async (limit = 10, offset = 0, classification = "all") => ({
      count: offset === 0 ? 10 : 1,
      total: classification === "fresh" ? 3 : 11,
      limit,
      offset,
      classification,
      classification_totals: { fresh: 3, decaying: 4, stale: 2, closed: 2 },
      edges: offset === 0 ? [edge(1)] : [edge(2)],
    }));

    render(<EdgesPage />);

    await screen.findByText("Edge event 1");
    expect(api.edgeMonitor).toHaveBeenCalledWith(10, 0, "all");
    expect(screen.getByText("\u7b2c 1 / 2 \u9875 \u00b7 \u5171 11 \u6761")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /\u4e0b\u4e00\u9875/ }));

    await waitFor(() => expect(api.edgeMonitor).toHaveBeenLastCalledWith(10, 10, "all"));
    expect(await screen.findByText("Edge event 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /\u4ecd\u63a5\u8fd1\u5cf0\u503c/ }));

    await waitFor(() => expect(api.edgeMonitor).toHaveBeenLastCalledWith(10, 0, "fresh"));
  });
});
