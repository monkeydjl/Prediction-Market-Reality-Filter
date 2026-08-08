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
});
