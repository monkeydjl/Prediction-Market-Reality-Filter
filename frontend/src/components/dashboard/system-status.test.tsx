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

  // O5: get_spend_today() -- the number LLM_DAILY_COST_CAP_USD is enforced
  // against -- had no reader outside the gateway, so an operator could not see
  // how close they were to having every LLM call refused.
  describe("每日 LLM 成本上限", () => {
    async function expandWith(costCap: unknown) {
      apiMocks.llmDiagnostics.mockResolvedValue({
        configured_task_count: 1,
        unconfigured_task_count: 0,
        tasks: [],
        cost_cap: costCap,
      });
      const user = userEvent.setup();
      render(<SystemStatus />);
      await user.click(screen.getByRole("button", { name: "展开系统状态" }));
      await waitFor(() => expect(apiMocks.llmDiagnostics).toHaveBeenCalled());
    }

    it("renders today's spend against the cap", async () => {
      await expandWith({
        enabled: true,
        cap_usd: 25,
        spend_today_usd: 5,
        remaining_usd: 20,
        used_ratio: 0.2,
        status: "ok",
        error: null,
      });

      expect(await screen.findByTestId("llm-cost-cap")).toBeInTheDocument();
      expect(screen.getByText("额度充足")).toBeInTheDocument();
      expect(
        screen.getByText("今日 $5.0000 / 上限 $25.0000 · 剩余 $20.0000"),
      ).toBeInTheDocument();
      expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "20");
    });

    it("marks a cap that has been reached", async () => {
      await expandWith({
        enabled: true,
        cap_usd: 25,
        spend_today_usd: 25,
        remaining_usd: 0,
        used_ratio: 1,
        status: "exceeded",
        error: null,
      });

      expect(await screen.findByText("已达上限")).toBeInTheDocument();
      expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    });

    // A disabled cap must not be painted as healthy -- that is the green-badge
    // mistake the discovery panel made for a source that was never asked.
    it("shows a disabled cap as neutral, not as a passing check", async () => {
      await expandWith({
        enabled: false,
        cap_usd: 0,
        spend_today_usd: null,
        remaining_usd: null,
        used_ratio: null,
        status: "disabled",
        error: null,
      });

      const card = await screen.findByTestId("llm-cost-cap");
      expect(card).toBeInTheDocument();
      expect(screen.getByText("未启用")).toBeInTheDocument();
      expect(screen.getByText("LLM_DAILY_COST_CAP_USD=0，不限额")).toBeInTheDocument();
      expect(screen.getByText("未启用").className).not.toMatch(/text-pos/);
      // No spend figure at all, rather than a $0.0000 that reads as "measured".
      expect(screen.queryByText(/今日 \$/)).not.toBeInTheDocument();
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    });

    it("surfaces an unreadable spend counter as a warning, not a pass", async () => {
      await expandWith({
        enabled: true,
        cap_usd: 25,
        spend_today_usd: null,
        remaining_usd: null,
        used_ratio: null,
        status: "unknown",
        error: "spend_lookup_failed",
      });

      expect(await screen.findByText("读数不可用")).toBeInTheDocument();
      expect(
        screen.getByText("读取今日花费失败（spend_lookup_failed）；上限仍在生效。"),
      ).toBeInTheDocument();
      expect(screen.getByText("今日 — / 上限 $25.0000 · 剩余 —")).toBeInTheDocument();
    });

    // Non-vacuous baseline: the whole card is optional, so a backend that does
    // not send the key must render exactly as before rather than showing a card
    // full of em dashes.
    it("renders nothing when the backend omits the block", async () => {
      await expandWith(undefined);

      expect(screen.getByText("LLM 网关路由")).toBeInTheDocument();
      expect(screen.queryByTestId("llm-cost-cap")).not.toBeInTheDocument();
    });
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
