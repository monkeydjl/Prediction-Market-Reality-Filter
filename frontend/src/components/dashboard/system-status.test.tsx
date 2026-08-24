import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SystemStatus } from "./system-status";

const apiMocks = vi.hoisted(() => ({
  health: vi.fn(),
  overview: vi.fn(),
  llmDiagnostics: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  eventsApi: apiMocks,
}));

describe("SystemStatus", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.health.mockResolvedValue({
      status: "ok",
      version: "test",
      loop: {
        scheduler: { running: true },
        runs: {},
        recent_runs: [],
        counts: {
          events: 3,
          resolved_events: 1,
          pending_links: 0,
          dangling_predictions: 0,
          dangling_links: 0,
          calibration_n: 2,
        },
      },
    });
    apiMocks.overview.mockResolvedValue({
      version: "test",
      endpoints: { events: {}, llm: {} },
    });
    apiMocks.llmDiagnostics.mockResolvedValue({
      configured_task_count: 1,
      unconfigured_task_count: 1,
      tasks: [
        {
          task: "probability_analysis",
          setting: "LLM_ROUTE_PROBABILITY_ANALYSIS",
          route_source: "task",
          configured: true,
          routes: [
            {
              provider: "deepseek",
              models: ["deepseek-reasoner", "deepseek-chat"],
              provider_configured: true,
              api_key_configured: true,
              base_url_configured: true,
            },
          ],
        },
        {
          task: "translation",
          setting: "LLM_ROUTE_TRANSLATION",
          route_source: "none",
          configured: false,
          routes: [],
        },
      ],
    });
  });

  it("shows redacted LLM gateway diagnostics in the expanded status panel", async () => {
    const user = userEvent.setup();
    render(<SystemStatus />);

    expect(await screen.findByText("LLM 路由 1/2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "展开系统状态" }));

    await waitFor(() => expect(apiMocks.llmDiagnostics).toHaveBeenCalled());
    expect(screen.getByText("LLM 网关路由")).toBeInTheDocument();
    expect(screen.getByText("概率分析")).toBeInTheDocument();
    expect(screen.getByText("LLM_ROUTE_PROBABILITY_ANALYSIS · 专用路由")).toBeInTheDocument();
    expect(screen.getByText("deepseek · 2 models · key yes")).toBeInTheDocument();
    expect(screen.getByText("标题翻译")).toBeInTheDocument();
    expect(screen.getByText("未解析到 provider/model 路由。")).toBeInTheDocument();
    expect(screen.queryByText(/secret/i)).not.toBeInTheDocument();
  });

  // E2: the 引用异常 badge summed dangling_predictions + dangling_links, which
  // covered two of the five tables carrying an event_id. The one genuinely
  // stranded row in the live database was an open simulated trade, so the badge
  // read 0 while a broken reference existed.
  describe("引用异常 badge", () => {
    /**
     * Render and wait until the fetched counts are on screen.
     *
     * The wait is load-bearing, not ceremony. On the first render `status` is
     * null, so the badge reads "引用异常 0" — `findByText("引用异常 0")` resolves
     * against that loading frame and returns before any data arrives, which
     * makes an assertion about a zero count pass no matter what the component
     * does with the payload. `事件 3` comes only from the fetched counts, so
     * awaiting it proves `setStatus` has landed.
     */
    const renderLoaded = async (counts: Record<string, unknown>) => {
      apiMocks.health.mockResolvedValue({
        status: "ok",
        version: "test",
        loop: {
          scheduler: { running: true },
          runs: {},
          recent_runs: [],
          counts: { events: 3, resolved_events: 1, pending_links: 0, ...counts },
        },
      });
      render(<SystemStatus />);
      await screen.findByText("事件 3");
      return screen.getByText(/^引用异常/);
    };

    it("reports the backend total even when the two legacy keys are zero", async () => {
      const badge = await renderLoaded({
        dangling_predictions: 0,
        dangling_links: 0,
        dangling_refs: 1,
        dangling_by_table: { simulated_trades: 1, predictions: 0 },
      });

      expect(badge).toHaveTextContent("引用异常 1");
    });

    it("names the offending table, since a bare total is not actionable", async () => {
      const badge = await renderLoaded({
        dangling_refs: 3,
        dangling_by_table: { simulated_trades: 1, review_queue_items: 2, predictions: 0 },
      });

      // Tables with no stranded row are left out rather than listed as ": 0".
      expect(badge).toHaveAttribute(
        "title",
        "simulated_trades: 1 · review_queue_items: 2",
      );
    });

    it("falls back to the two legacy keys when the total is absent", async () => {
      const badge = await renderLoaded({
        dangling_predictions: 2,
        dangling_links: 1,
      });

      expect(badge).toHaveTextContent("引用异常 3");
    });

    it("shows zero rather than the fallback sum when the backend reports zero", async () => {
      // `??` not `||`: a real 0 from the backend must win over the legacy sum,
      // which is stale in the other direction on an old payload.
      const badge = await renderLoaded({
        dangling_predictions: 5,
        dangling_links: 5,
        dangling_refs: 0,
        dangling_by_table: {},
      });

      expect(badge).toHaveTextContent("引用异常 0");
      expect(badge).toHaveAttribute("title", "没有指向已删除事件的残留行");
    });
  });
});
