import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const engineMocks = vi.hoisted(() => ({
  batchPredict: vi.fn(),
  batchSwitchEngine: vi.fn(),
  batchOptimize: vi.fn(),
  autoTune: vi.fn(),
  autoTuneStatus: vi.fn(),
  calibration: vi.fn(),
  calibrationPatterns: vi.fn(),
  streamBatchSwitchEngine: vi.fn(),
}));

vi.mock("@/lib/world-cup/engine-api", () => ({
  engineApi: {
    batchPredict: engineMocks.batchPredict,
    batchSwitchEngine: engineMocks.batchSwitchEngine,
    batchOptimize: engineMocks.batchOptimize,
    autoTune: engineMocks.autoTune,
    autoTuneStatus: engineMocks.autoTuneStatus,
    calibration: engineMocks.calibration,
    calibrationPatterns: engineMocks.calibrationPatterns,
  },
  streamBatchSwitchEngine: engineMocks.streamBatchSwitchEngine,
}));

import { EngineConsole } from "./engine-console";

describe("EngineConsole", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    engineMocks.streamBatchSwitchEngine.mockResolvedValue(undefined);
  });

  it("requires a second confirmation before running a batch operation", async () => {
    render(<EngineConsole />);

    await userEvent.click(screen.getByTestId("batch-predict-button"));
    expect(screen.getByTestId("engine-confirm")).toBeInTheDocument();
    expect(engineMocks.batchPredict).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("engine-confirm-no"));
    expect(screen.queryByTestId("engine-confirm")).not.toBeInTheDocument();
    expect(engineMocks.batchPredict).not.toHaveBeenCalled();
  });

  it("runs the batch predict and reports the summary after confirming", async () => {
    engineMocks.batchPredict.mockResolvedValue({
      total: 12,
      succeeded: 10,
      failed: 1,
      skipped: 1,
    });
    render(<EngineConsole />);

    await userEvent.click(screen.getByTestId("batch-predict-button"));
    await userEvent.click(screen.getByTestId("engine-confirm-yes"));

    expect(engineMocks.batchPredict).toHaveBeenCalledWith("elo_odds");
    expect(await screen.findByText(/共 12 \/ 成功 10 \/ 失败 1 \/ 跳过 1/)).toBeInTheDocument();
  });

  it("shows a bucket for every engine the batch summary reports", async () => {
    // Regression: `gbm` had no bucket on the backend, so a gbm batch rendered
    // "共 4 / 成功 4" with no engine tallies at all and looked like a no-op.
    // The console must render whichever buckets the summary actually carries.
    engineMocks.batchPredict.mockResolvedValue({
      total: 4,
      succeeded: 4,
      failed: 0,
      skipped: 0,
      elo_odds_count: 1,
      hybrid_count: 1,
      integrated_count: 1,
      gbm_count: 1,
    });
    render(<EngineConsole />);

    await userEvent.click(screen.getByTestId("batch-predict-button"));
    await userEvent.click(screen.getByTestId("engine-confirm-yes"));

    const summary = await screen.findByTestId("engine-summary");
    expect(summary).toHaveTextContent("ELO 1");
    expect(summary).toHaveTextContent("混合 1");
    expect(summary).toHaveTextContent("融合 1");
    expect(summary).toHaveTextContent("GBM 1");
  });

  it("offers gbm in the engine selector and switches to it", async () => {
    // The selector stopped at high_confidence while the backend already
    // accepted gbm, so an operator could read GBM results but never ask for
    // them. The option list is pinned in order, not just checked for the
    // presence of gbm, so a reorder that drops one engine still fails here.
    render(<EngineConsole />);

    const select = screen.getByTestId("engine-select");
    const options = Array.from(select.querySelectorAll("option")).map(
      (o) => (o as HTMLOptionElement).value,
    );
    expect(options).toEqual([
      "elo_odds",
      "hybrid",
      "integrated",
      "high_confidence",
      "gbm",
    ]);

    await userEvent.selectOptions(select, "gbm");
    await userEvent.click(screen.getByTestId("batch-switch-button"));
    await userEvent.click(screen.getByTestId("engine-confirm-yes"));

    expect(engineMocks.streamBatchSwitchEngine).toHaveBeenCalledWith(
      "gbm",
      "scheduled",
      expect.objectContaining({ onProgress: expect.any(Function) }),
    );
  });

  it("offers gbm among the tunable engines and tunes it", async () => {
    // Same gap as the switch selector, one layer up: the backend tuning and
    // calibration routes accept gbm (auto-tune / batch-optimize fall through
    // `engine_method_filter`'s alias branches to a `LIKE %name%` match, and
    // calibration matches `engine_name` by equality), so an operator could see
    // GBM batch results but had no way to tune or calibrate GBM. The option
    // list is pinned in order, so a reorder that drops an engine still fails.
    engineMocks.calibration.mockResolvedValue({
      status: "ok",
      calibration: { engine_name: "gbm" },
    });
    render(<EngineConsole />);

    const select = screen.getByTestId("tune-engine-select");
    const options = Array.from(select.querySelectorAll("option")).map(
      (o) => (o as HTMLOptionElement).value,
    );
    expect(options).toEqual(["elo_odds", "hybrid", "integrated", "gbm"]);

    await userEvent.selectOptions(select, "gbm");
    await userEvent.click(screen.getByTestId("calibration-button"));

    expect(engineMocks.calibration).toHaveBeenCalledWith("gbm");
    expect(await screen.findByTestId("calibration-result")).toBeInTheDocument();
  });

  it("renders streamed progress and the final summary for the engine switch", async () => {
    engineMocks.streamBatchSwitchEngine.mockImplementation(
      async (_engine: string, _filter: string, handlers: Record<string, (p: unknown) => void>) => {
        handlers.onStart?.({ total: 4, engine: "hybrid" });
        handlers.onProgress?.({
          current: 2,
          total: 4,
          match_id: "wc-7",
          succeeded: 2,
          failed: 0,
          skipped: 0,
        });
        handlers.onComplete?.({ total: 4, succeeded: 4, failed: 0, skipped: 0 });
      },
    );
    render(<EngineConsole />);

    await userEvent.selectOptions(screen.getByTestId("engine-select"), "hybrid");
    await userEvent.click(screen.getByTestId("batch-switch-button"));
    await userEvent.click(screen.getByTestId("engine-confirm-yes"));

    expect(engineMocks.streamBatchSwitchEngine).toHaveBeenCalledWith(
      "hybrid",
      "scheduled",
      expect.objectContaining({ onProgress: expect.any(Function) }),
    );
    expect(await screen.findByText(/2 \/ 4 · wc-7/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "2");
    expect(await screen.findByText(/共 4 \/ 成功 4/)).toBeInTheDocument();
  });

  it("surfaces a stream error without leaving the console busy", async () => {
    engineMocks.streamBatchSwitchEngine.mockImplementation(
      async (_e: string, _f: string, handlers: Record<string, (p: unknown) => void>) => {
        handlers.onError?.("需要有效的操作员 API Key");
      },
    );
    render(<EngineConsole />);

    await userEvent.click(screen.getByTestId("batch-switch-button"));
    await userEvent.click(screen.getByTestId("engine-confirm-yes"));

    expect(await screen.findByTestId("engine-console-error")).toHaveTextContent(
      "需要有效的操作员 API Key",
    );
    await waitFor(() => expect(screen.getByTestId("batch-predict-button")).toBeEnabled());
  });

  it("polls the auto-tune task until it completes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    engineMocks.autoTune.mockResolvedValue({ status: "accepted", task_id: "t-1" });
    engineMocks.autoTuneStatus.mockResolvedValue({
      task: { task_id: "t-1", status: "completed", stage: "done", progress: 1 },
    });

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<EngineConsole />);
    await user.click(screen.getByTestId("auto-tune-button"));

    expect(await screen.findByTestId("auto-tune-task")).toHaveTextContent("t-1");

    await vi.advanceTimersByTimeAsync(3_000);
    await waitFor(() =>
      expect(screen.getByTestId("auto-tune-task")).toHaveTextContent("completed"),
    );

    const callsAfterCompletion = engineMocks.autoTuneStatus.mock.calls.length;
    await vi.advanceTimersByTimeAsync(9_000);
    expect(engineMocks.autoTuneStatus.mock.calls.length).toBe(callsAfterCompletion);

    vi.useRealTimers();
  });

  it("explains an empty calibration instead of rendering a blank block", async () => {
    engineMocks.calibration.mockResolvedValue({
      status: "not_found",
      message: "该引擎暂无启用中的标定参数。",
    });
    render(<EngineConsole />);

    await userEvent.click(screen.getByTestId("calibration-button"));

    expect(await screen.findByTestId("calibration-result")).toHaveTextContent(
      "该引擎暂无启用中的标定参数。",
    );
  });

  it("renders the optimisation-pattern analysis the backend actually sends", async () => {
    // Regression (audit 53.71): the hand-written `CalibrationPatterns` declared
    // `sample_count` / `patterns` / `suggestions`, none of which the producer
    // has ever sent, so the old `patterns.patterns || patterns.suggestions`
    // guard was permanently false and this panel could only ever show its
    // fallback. The fixture below is the real `ok` shape from
    // `engine_auto_tuning_service.calculate_optimization_patterns`.
    engineMocks.calibrationPatterns.mockResolvedValue({
      status: "ok",
      engine: "elo_odds",
      samples: 42,
      unverified_samples: 7,
      avg_improvement: 0.31,
      calibration_params: {
        home_score_bias: 0.356,
        away_score_bias: -0.259,
        home_win_prob_shift: 0.097,
        draw_prob_shift: 0.024,
        away_win_prob_shift: -0.123,
        confidence_multiplier: 0.987,
      },
      top_blind_spots: [["draw_underestimated", 5]],
      top_calibration_issues: [["low_sample_bucket", 2]],
      analysis: {
        avg_home_score_adjustment: 0.356,
        avg_away_score_adjustment: -0.259,
        avg_home_win_prob_adjustment: 0.097,
        avg_draw_prob_adjustment: 0.024,
        avg_away_win_prob_adjustment: -0.123,
        avg_confidence_adjustment: -0.013,
      },
    });
    render(<EngineConsole />);

    await userEvent.click(screen.getByTestId("patterns-button"));

    const panel = await screen.findByTestId("patterns-result");
    expect(panel).toHaveTextContent("home_score_bias");
    expect(panel).toHaveTextContent("0.356");
    expect(panel).toHaveTextContent("draw_underestimated");
    expect(panel).not.toHaveTextContent("样本不足，暂无可用的优化模式。");
  });

  it("explains an unverified pattern analysis instead of rendering a blank block", async () => {
    // The other side of the same panel: the `unverified` shape carries no
    // `calibration_params`, so the guard must fall through to `message`.
    engineMocks.calibrationPatterns.mockResolvedValue({
      status: "unverified",
      engine: "hybrid",
      samples: 0,
      unverified_samples: 12,
      message: "AI optimizations for engine 'hybrid' have not been verified",
    });
    render(<EngineConsole />);

    await userEvent.click(screen.getByTestId("patterns-button"));

    expect(await screen.findByTestId("patterns-result")).toHaveTextContent(
      "have not been verified",
    );
  });
});
