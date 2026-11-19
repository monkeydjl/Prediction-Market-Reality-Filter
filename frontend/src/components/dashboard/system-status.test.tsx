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
     * The wait is load-bearing, not ceremony: `事件 3` comes only from the
     * fetched counts, so awaiting it proves `setStatus` has landed. It used to
     * be doubly load-bearing for a reason worth keeping on record -- before
     * 53.133 the loading frame also read "引用异常 0", so
     * `findByText("引用异常 0")` could resolve against the frame *before* any
     * data arrived and make a zero assertion pass no matter what the component
     * did with the payload. That trap is gone (the frame reads "—" now), but
     * the wait stays: it is what keeps every assertion here about the payload
     * rather than about the first paint.
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

    // 53.95: the backend total counts history and never returns to zero after a
    // purge, so the badge reads the actionable subset and the total moves into
    // the tooltip. Both halves are pinned here: the zero case and the case where
    // the two readings differ in the other direction.
    it("reads the actionable subtotal, not the historical total", async () => {
      const badge = await renderLoaded({
        dangling_refs: 1,
        dangling_by_table: { simulated_trades: 1, predictions: 0 },
        actionable_dangling_refs: 0,
        actionable_dangling_by_table: { simulated_trades: 0 },
      });

      expect(badge).toHaveTextContent("引用异常 0");
      expect(badge).toHaveAttribute(
        "title",
        "无待处置项 · 历史悬空引用 1（simulated_trades: 1）",
      );
    });

    it("names only the tables that still need work when there is any", async () => {
      const badge = await renderLoaded({
        dangling_refs: 4,
        dangling_by_table: { simulated_trades: 3, review_queue_items: 1 },
        actionable_dangling_refs: 1,
        actionable_dangling_by_table: { simulated_trades: 1, review_queue_items: 0 },
      });

      expect(badge).toHaveTextContent("引用异常 1");
      expect(badge).toHaveAttribute("title", "simulated_trades: 1");
    });

    it("falls back to the historical total when the actionable key is absent", async () => {
      // An older backend must not make the badge read 0 over a real broken
      // reference: absent means unknown, not "none".
      const badge = await renderLoaded({
        dangling_refs: 2,
        dangling_by_table: { predictions: 2 },
      });

      expect(badge).toHaveTextContent("引用异常 2");
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

  // 53.133: the badge ended both fallbacks in `?? 0`, so two different unknowns
  // were painted as the same number: "the question has not been asked yet" and
  // "this backend sends none of the three keys". §5 does not allow a reader to
  // mistake one of those for "there are no broken references". Each case below
  // asserts the "—" *and* the absence of the zero, because a badge that always
  // says "—" would pass the first half alone.
  describe("引用异常 的三个读数", () => {
    it("does not print a zero before the answer arrives", async () => {
      apiMocks.health.mockReturnValue(new Promise(() => {}));
      render(<SystemStatus />);
      await waitFor(() => expect(apiMocks.health).toHaveBeenCalled());

      expect(screen.getByText(/^引用异常/)).toHaveTextContent("引用异常 —");
      expect(screen.queryByText("引用异常 0")).not.toBeInTheDocument();
    });

    it("shows — when counts carries none of the three keys", async () => {
      // A backend that rewrote the block, or a proxy that trimmed it: absence
      // is not zero, and summing two missing keys into "0" is a guess.
      apiMocks.health.mockResolvedValue({
        status: "ok",
        version: "test",
        loop: {
          scheduler: { running: true },
          runs: {},
          recent_runs: [],
          counts: { events: 3, resolved_events: 1, pending_links: 0 },
        },
      });
      render(<SystemStatus />);
      await screen.findByText("事件 3");

      expect(screen.getByText(/^引用异常/)).toHaveTextContent("引用异常 —");
      expect(screen.queryByText("引用异常 0")).not.toBeInTheDocument();
    });

    it("shows — when the loop block has no counts at all", async () => {
      apiMocks.health.mockResolvedValue({
        status: "ok",
        version: "test",
        loop: { scheduler: { running: true }, runs: {}, recent_runs: [] },
      });
      render(<SystemStatus />);
      await waitFor(() => expect(apiMocks.health).toHaveBeenCalled());

      expect(await screen.findByText(/^引用异常/)).toHaveTextContent("引用异常 —");
    });
  });

  // 53.126: the producer has said *which* zero it is since §53.116–§53.119, and
  // nothing in `frontend/src` read any of those three switches -- the panel
  // showed a confident 0 for an empty table and for a broken query alike.
  //
  // Each case below asserts both directions on purpose. A badge that always
  // prints 「读取失败」 would pass every failure case here and quietly destroy
  // the all-clear reading, so the "everything is fine" case pins the numbers.
  describe("§5 门控：这个 0 是真的吗", () => {
    /**
     * `事件 3` is the same ready-marker the dangling tests use: the first render
     * frame has `status === null`, where these badges already say `预测 —`, so
     * awaiting a badge text would resolve against the loading frame and pass no
     * matter what the payload said.
     */
    const renderLoaded = async (
      counts: Record<string, unknown>,
      reviewQueue?: Record<string, unknown>,
    ) => {
      apiMocks.health.mockResolvedValue({
        status: "ok",
        version: "test",
        loop: {
          scheduler: { running: true },
          runs: {},
          recent_runs: [],
          counts: { events: 3, resolved_events: 1, pending_links: 0, ...counts },
          ...(reviewQueue ? { review_queue: reviewQueue } : {}),
        },
      });
      render(<SystemStatus />);
      await screen.findByText("事件 3");
    };

    it("renders a real zero as zero when all three switches are true", async () => {
      await renderLoaded(
        {
          predictions: { open: 0 },
          predictions_available: true,
          pending_reviews: 0,
          breached_reviews: 0,
        },
        { available: true, enabled: true },
      );

      expect(screen.getByText("预测 0")).toBeInTheDocument();
      expect(screen.getByText("审核待办 0")).toBeInTheDocument();
      expect(screen.getByText("SLA 逾期 0")).toBeInTheDocument();
      expect(screen.queryByText(/读取失败/)).not.toBeInTheDocument();
      expect(screen.queryByText(/未启用/)).not.toBeInTheDocument();
    });

    it("a failed predictions read is not a zero", async () => {
      await renderLoaded({ predictions: {}, predictions_available: false });

      expect(screen.getByText("预测 读取失败")).toBeInTheDocument();
      expect(screen.queryByText("预测 0")).not.toBeInTheDocument();
    });

    it("a failed review-queue read is not a zero, on either number", async () => {
      // One switch for the pair: `pending_reviews` / `breached_reviews` are
      // copied out of this block, so reading `counts` alone cannot tell.
      await renderLoaded(
        { pending_reviews: 0, breached_reviews: 0 },
        { available: false, enabled: true },
      );

      expect(screen.getByText("审核待办 读取失败")).toBeInTheDocument();
      expect(screen.getByText("SLA 逾期 读取失败")).toBeInTheDocument();
      expect(screen.queryByText("审核待办 0")).not.toBeInTheDocument();
    });

    it("a disabled queue says so instead of reporting a failed read", async () => {
      // `enabled: false` is a configuration, not a defect: nothing is ever
      // enqueued, so 0 is the correct answer and calling it a failed read would
      // send the operator to a log that has nothing in it.
      await renderLoaded(
        { pending_reviews: 0, breached_reviews: 0 },
        { available: true, enabled: false },
      );

      expect(screen.getByText("审核待办 未启用")).toBeInTheDocument();
      expect(screen.getByText("SLA 逾期 未启用")).toBeInTheDocument();
      expect(screen.queryByText(/读取失败/)).not.toBeInTheDocument();
    });

    it("keeps the failed-read fact in the tooltip when the queue is also off", async () => {
      await renderLoaded(
        { pending_reviews: 0, breached_reviews: 0 },
        { available: false, enabled: false },
      );

      const badge = screen.getByText("审核待办 未启用");
      expect(badge).toHaveAttribute("title", expect.stringContaining("本次读取也失败了"));
    });

    it("falls back to the numbers when an older backend sends no switch", async () => {
      // §5: absent means unknown, not false. A panel that printed 「读取失败」
      // for a backend predating the key would be a permanent false alarm.
      await renderLoaded({
        predictions: { open: 2, resolved: 1 },
        pending_reviews: 4,
        breached_reviews: 1,
      });

      expect(screen.getByText("预测 3")).toHaveAttribute("title", "open: 2 · resolved: 1");
      expect(screen.getByText("审核待办 4")).toBeInTheDocument();
      expect(screen.getByText("SLA 逾期 1")).toBeInTheDocument();
      expect(screen.queryByText(/读取失败|未启用/)).not.toBeInTheDocument();
    });
  });

  // 53.128: the badge only totals `predictions`, and `orphan_predictions` had no
  // reader at all -- which is how a number that means "an event settled while
  // its prediction stayed open" came to be named "孤儿" and never shown.
  describe("预测台账与结算未收口", () => {
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
    };

    it("names the status breakdown, biggest first", async () => {
      await renderLoaded(
        {
          predictions: { open: 49, scored: 2, observed: 119, voided: 1 },
          predictions_available: true,
        },
      );

      const chips = screen.getAllByTestId(/^prediction-status-/).map((el) => el.textContent);
      expect(chips).toEqual(["已观测 119", "进行中 49", "已评分 2", "已作废 1"]);
      expect(screen.getByText("共 171 条")).toBeInTheDocument();
      // The scored/observed split is not cosmetic: both mean "the event
      // resolved", but only act rows enter the calibration signal.
      expect(screen.getByTestId("prediction-status-observed")).toHaveAttribute(
        "title",
        expect.stringContaining("不进校准"),
      );
    });

    it("renders a status the panel does not know by name", async () => {
      // The map's vocabulary can grow; a chip that vanished would hide rows.
      await renderLoaded({ predictions: { open: 1, quarantined: 3 }, predictions_available: true });

      expect(screen.getByTestId("prediction-status-quarantined")).toHaveTextContent("quarantined 3");
    });

    it("replaces the chips with the log lines when the read failed", async () => {
      await renderLoaded({ predictions: {}, predictions_available: false, orphan_predictions: 0 });

      expect(screen.getByText(/prediction status counts unavailable/)).toBeInTheDocument();
      expect(screen.getByText("这次没读到")).toBeInTheDocument();
      expect(screen.queryAllByTestId(/^prediction-status-/)).toHaveLength(0);
      expect(screen.queryByText("共 0 条")).not.toBeInTheDocument();
    });

    it("says the ledger is empty rather than rendering an empty row", async () => {
      // The other zero: the read worked and there is genuinely nothing here.
      await renderLoaded({ predictions: {}, predictions_available: true });

      expect(screen.getByText("台账里还没有预测。")).toBeInTheDocument();
      expect(screen.getByText("共 0 条")).toBeInTheDocument();
    });

    it("gates the settlement-left-open badge and says what it counts", async () => {
      await renderLoaded({ predictions: { open: 49 }, predictions_available: true, orphan_predictions: 2 });

      expect(screen.getByText("结算未收口 2")).toHaveAttribute(
        "title",
        expect.stringContaining("已结算事件"),
      );
    });

    it("a failed read does not turn the settlement count into a zero", async () => {
      await renderLoaded({ predictions: {}, predictions_available: false, orphan_predictions: 0 });

      expect(screen.getByText("结算未收口 读取失败")).toBeInTheDocument();
      expect(screen.queryByText("结算未收口 0")).not.toBeInTheDocument();
    });

    // 53.132: it used to live inside the collapse, which put the panel's most
    // concrete content one hover away -- and a tooltip is unreachable on touch.
    it("shows the distribution without expanding the panel", async () => {
      await renderLoaded({
        predictions: { open: 49, scored: 2, observed: 119, voided: 1 },
        predictions_available: true,
      });

      // The toggle still reads 展开, so the chips below cannot have come from
      // expanding it -- that is the whole assertion.
      expect(screen.getByRole("button", { name: "展开系统状态" })).toBeInTheDocument();
      expect(screen.getByText("预测台账")).toBeInTheDocument();
      expect(screen.getByText("共 171 条")).toBeInTheDocument();
      expect(screen.getByTestId("prediction-status-observed")).toHaveTextContent("已观测 119");
    });

    it("does not render the ledger before the answer arrives", async () => {
      // `status === null` means "not asked yet". A 「共 0 条」 in that frame is
      // the same confident zero as a failed read (§5), so this block waits for
      // the payload instead of drawing a placeholder.
      apiMocks.health.mockReturnValue(new Promise(() => {}));
      render(<SystemStatus />);
      await waitFor(() => expect(apiMocks.health).toHaveBeenCalled());

      expect(screen.queryByText("预测台账")).not.toBeInTheDocument();
      expect(screen.queryByText("共 0 条")).not.toBeInTheDocument();
      expect(screen.queryAllByTestId(/^prediction-status-/)).toHaveLength(0);
    });
  });
});
