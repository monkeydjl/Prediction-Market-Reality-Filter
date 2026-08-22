import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  ScatterChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="scatter-chart">{children}</div>
  ),
  Scatter: ({ data }: { data: unknown[] }) => (
    <div data-testid="scatter" data-count={data.length} />
  ),
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
  ReferenceLine: () => <div data-testid="reference-line" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

vi.mock("@/components/ui/chart-lite", () => ({
  ChartFrame: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="chart-frame">{children}</div>
  ),
  DarkTooltip: () => <div data-testid="dark-tooltip" />,
}));

const apiMocks = vi.hoisted(() => ({
  useCalibration: vi.fn(),
  useReliability: vi.fn(),
  useConfidenceReliability: vi.fn(),
  refreshConditionalCalibration: vi.fn(),
}));
vi.mock("@/lib/sports-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sports-api")>()),
  useCalibration: apiMocks.useCalibration,
  useReliability: apiMocks.useReliability,
  useConfidenceReliability: apiMocks.useConfidenceReliability,
  refreshConditionalCalibration: apiMocks.refreshConditionalCalibration,
}));

import { CalibrationPanel } from "./calibration-panel";

const mockCal = {
  engine: "basketball",
  competition: "nba",
  slope: 0.85,
  intercept: 0.05,
  sample_count: 48,
  avg_confidence: 0.62,
  avg_accuracy: 0.625,
  last_updated: "2026-07-14T18:30:00Z",
};

const mockReliability = {
  engine: null,
  competition: null,
  bins: [
    { lower: 0.5, upper: 0.6, center: 0.55, avg_predicted: 0.58, actual_frequency: 0.55, count: 12 },
  ],
  total_samples: 48,
};

describe("CalibrationPanel", () => {
  beforeEach(() => {
    apiMocks.useCalibration.mockReset();
    apiMocks.useReliability.mockReset();
    apiMocks.refreshConditionalCalibration.mockReset();
    // Default: the confidence curve is still loading, so the assertions below
    // that expect a single scatter chart stay unambiguous. The confidence
    // section has its own cases at the bottom of this file.
    apiMocks.useConfidenceReliability.mockReset();
    apiMocks.useConfidenceReliability.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });
  });

  it("renders parameter table with calibration data", async () => {
    apiMocks.useCalibration.mockReturnValue({
      data: [mockCal],
      error: undefined,
      isLoading: false,
    });
    apiMocks.useReliability.mockReturnValue({
      data: mockReliability,
      error: undefined,
      isLoading: false,
    });
    render(<CalibrationPanel />);
    await waitFor(() => {
      // Use getByRole("cell") to disambiguate from <option> elements with same text
      expect(screen.getByRole("cell", { name: "basketball" })).toBeInTheDocument();
      expect(screen.getByRole("cell", { name: "0.85" })).toBeInTheDocument();
    });
  });

  it("renders reliability chart", async () => {
    apiMocks.useCalibration.mockReturnValue({
      data: [mockCal],
      error: undefined,
      isLoading: false,
    });
    apiMocks.useReliability.mockReturnValue({
      data: mockReliability,
      error: undefined,
      isLoading: false,
    });
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("scatter")).toBeInTheDocument();
    });
  });

  it("renders empty state for calibration when no data", async () => {
    apiMocks.useCalibration.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
    });
    apiMocks.useReliability.mockReturnValue({
      data: { ...mockReliability, total_samples: 0, bins: [] },
      error: undefined,
      isLoading: false,
    });
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByText("暂无校准数据，需 ≥ MIN_SAMPLES_FOR_CALIBRATION 条记录")).toBeInTheDocument();
    });
  });

  it("renders filter dropdowns", async () => {
    apiMocks.useCalibration.mockReturnValue({
      data: [],
      error: undefined,
      isLoading: false,
    });
    apiMocks.useReliability.mockReturnValue({
      data: { ...mockReliability, total_samples: 0, bins: [] },
      error: undefined,
      isLoading: false,
    });
    render(<CalibrationPanel />);
    await waitFor(() => {
      expect(screen.getByText("引擎")).toBeInTheDocument();
    });
  });

  describe("confidence reliability section (P1-X1)", () => {
    const ok = { data: [mockCal], error: undefined, isLoading: false };

    it("renders a second chart bound to the confidence curve", async () => {
      apiMocks.useCalibration.mockReturnValue(ok);
      apiMocks.useReliability.mockReturnValue({
        data: mockReliability,
        error: undefined,
        isLoading: false,
      });
      apiMocks.useConfidenceReliability.mockReturnValue({
        data: {
          ...mockReliability,
          bins: [
            { lower: 0.8, upper: 0.9, center: 0.85, avg_predicted: 0.9, actual_frequency: 0.25, count: 4 },
            { lower: 0.4, upper: 0.5, center: 0.45, avg_predicted: 0.45, actual_frequency: 0.5, count: 2 },
          ],
          ece: 0.65,
          mean_confidence: 0.9,
          mean_accuracy: 0.25,
          signed_gap: 0.65,
        },
        error: undefined,
        isLoading: false,
      });
      render(<CalibrationPanel />);
      await waitFor(() => {
        // Two independent curves, not one rendered twice: the probability chart
        // has 1 non-empty bin, the confidence chart has 2.
        const counts = screen.getAllByTestId("scatter").map((n) => n.getAttribute("data-count"));
        expect(counts).toEqual(["1", "2"]);
      });
    });

    it("labels a positive gap 过度自信 and shows both means", async () => {
      apiMocks.useCalibration.mockReturnValue(ok);
      apiMocks.useReliability.mockReturnValue({
        data: mockReliability,
        error: undefined,
        isLoading: false,
      });
      apiMocks.useConfidenceReliability.mockReturnValue({
        data: { ...mockReliability, mean_confidence: 0.9, mean_accuracy: 0.25, signed_gap: 0.65 },
        error: undefined,
        isLoading: false,
      });
      render(<CalibrationPanel />);
      await waitFor(() => {
        const gap = screen.getByTestId("confidence-signed-gap");
        expect(gap.textContent).toContain("过度自信");
        expect(gap.textContent).toContain("+65.0pp");
        expect(gap.textContent).toContain("平均置信度 90.0%");
        expect(gap.textContent).toContain("平均准确率 25.0%");
      });
    });

    it("labels a negative gap 保守", async () => {
      apiMocks.useCalibration.mockReturnValue(ok);
      apiMocks.useReliability.mockReturnValue({
        data: mockReliability,
        error: undefined,
        isLoading: false,
      });
      apiMocks.useConfidenceReliability.mockReturnValue({
        data: { ...mockReliability, mean_confidence: 0.4, mean_accuracy: 1.0, signed_gap: -0.6 },
        error: undefined,
        isLoading: false,
      });
      render(<CalibrationPanel />);
      await waitFor(() => {
        const gap = screen.getByTestId("confidence-signed-gap");
        expect(gap.textContent).toContain("保守");
        expect(gap.textContent).toContain("-60.0pp");
      });
    });

    it("omits the gap readout when there are no graded samples", async () => {
      apiMocks.useCalibration.mockReturnValue(ok);
      apiMocks.useReliability.mockReturnValue({
        data: mockReliability,
        error: undefined,
        isLoading: false,
      });
      apiMocks.useConfidenceReliability.mockReturnValue({
        data: {
          ...mockReliability,
          bins: [],
          total_samples: 0,
          ece: null,
          mean_confidence: null,
          mean_accuracy: null,
          signed_gap: null,
        },
        error: undefined,
        isLoading: false,
      });
      render(<CalibrationPanel />);
      await waitFor(() => {
        expect(screen.getByText("置信度可靠性图")).toBeInTheDocument();
      });
      expect(screen.queryByTestId("confidence-signed-gap")).toBeNull();
    });

    it("one failing curve does not hide the other", async () => {
      apiMocks.useCalibration.mockReturnValue(ok);
      apiMocks.useReliability.mockReturnValue({
        data: mockReliability,
        error: undefined,
        isLoading: false,
      });
      apiMocks.useConfidenceReliability.mockReturnValue({
        data: undefined,
        error: new Error("503"),
        isLoading: false,
      });
      render(<CalibrationPanel />);
      await waitFor(() => {
        expect(screen.getByText("置信度可靠性数据加载失败")).toBeInTheDocument();
      });
      expect(screen.getAllByTestId("scatter")).toHaveLength(1);
    });
  });

  describe("conditional calibration buckets (P1-V5)", () => {
    const bucketRows = [
      mockCal,
      { ...mockCal, competition: "nba#c_high", slope: 0.71 },
      { ...mockCal, competition: "nba#s_regular", slope: 0.93 },
      { ...mockCal, competition: "epl", slope: 0.99 },
    ];

    function mountWith(rows: typeof bucketRows) {
      apiMocks.useCalibration.mockReturnValue({
        data: rows,
        error: undefined,
        isLoading: false,
      });
      apiMocks.useReliability.mockReturnValue({
        data: mockReliability,
        error: undefined,
        isLoading: false,
      });
      render(<CalibrationPanel />);
    }

    it("分桶列把复合键拆成赛事 + 分桶，不再原样打印 nba#c_high", async () => {
      mountWith(bucketRows);
      await waitFor(() => {
        expect(screen.getByTestId("cal-bucket-nba").textContent).toBe("基准");
      });
      expect(screen.getByTestId("cal-bucket-nba#c_high").textContent).toBe("置信度·高");
      expect(screen.getByTestId("cal-bucket-nba#s_regular").textContent).toBe("阶段·常规赛");
      // 赛事列显示 base，复合键只留在 title 里备查
      expect(screen.getAllByRole("cell", { name: "nba" })).toHaveLength(3);
      expect(screen.getByTestId("cal-bucket-nba#c_high").getAttribute("title")).toBe(
        "nba#c_high",
      );
    });

    it("选中赛事时分桶行仍在表内——服务端等值过滤会把它们全部藏掉", async () => {
      mountWith(bucketRows);
      await waitFor(() => expect(screen.getByTestId("cal-bucket-nba")).toBeInTheDocument());

      fireEvent.change(screen.getByLabelText("赛事"), { target: { value: "nba" } });

      await waitFor(() => {
        // epl 行被本地过滤掉，nba 的三行（基准 + 两个分桶）全部保留
        expect(screen.queryByTestId("cal-bucket-epl")).toBeNull();
      });
      expect(screen.getByTestId("cal-bucket-nba")).toBeInTheDocument();
      expect(screen.getByTestId("cal-bucket-nba#c_high")).toBeInTheDocument();
      expect(screen.getByTestId("cal-bucket-nba#s_regular")).toBeInTheDocument();
      // 关键：参数表的请求不再带 competition，过滤发生在前端
      const lastCall =
        apiMocks.useCalibration.mock.calls[apiMocks.useCalibration.mock.calls.length - 1][0];
      expect(lastCall).toEqual({ engine: undefined });
    });

    it("未选定具体引擎与赛事时拟合按钮禁用并说明原因", async () => {
      mountWith(bucketRows);
      await waitFor(() =>
        expect(screen.getByTestId("refresh-conditional")).toBeInTheDocument(),
      );
      expect(screen.getByTestId("refresh-conditional")).toBeDisabled();
      expect(screen.getByTestId("refresh-conditional-hint").textContent).toContain(
        "「全部」无法拟合",
      );
    });

    it("选定引擎与赛事后确认拟合，并渲染每个分桶的写入条数", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      apiMocks.refreshConditionalCalibration.mockResolvedValue({
        competition: "nba",
        engine: "basketball",
        confidence_buckets: { low: 0, mid: 12, high: 30 },
        stage_buckets: { regular: 25, knockout: 0, unknown: 0 },
      });
      mountWith(bucketRows);
      await waitFor(() =>
        expect(screen.getByTestId("refresh-conditional")).toBeInTheDocument(),
      );
      fireEvent.change(screen.getByLabelText("引擎"), { target: { value: "basketball" } });
      fireEvent.change(screen.getByLabelText("赛事"), { target: { value: "nba" } });
      await waitFor(() =>
        expect(screen.getByTestId("refresh-conditional")).not.toBeDisabled(),
      );

      await userEvent.click(screen.getByTestId("refresh-conditional"));

      expect(apiMocks.refreshConditionalCalibration).toHaveBeenCalledWith("nba", "basketball");
      await waitFor(() => {
        const out = screen.getByTestId("conditional-result").textContent ?? "";
        expect(out).toContain("mid=12 条");
        expect(out).toContain("high=30 条");
        // 0 条不是失败，是样本不足；写成 0 会被误读为拟合出了空参数
        expect(out).toContain("low=样本不足");
        expect(out).toContain("knockout=样本不足");
      });
      confirmSpy.mockRestore();
    });

    it("取消确认对话框时不发请求", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
      mountWith(bucketRows);
      await waitFor(() =>
        expect(screen.getByTestId("refresh-conditional")).toBeInTheDocument(),
      );
      fireEvent.change(screen.getByLabelText("引擎"), { target: { value: "basketball" } });
      fireEvent.change(screen.getByLabelText("赛事"), { target: { value: "nba" } });
      await userEvent.click(screen.getByTestId("refresh-conditional"));
      expect(apiMocks.refreshConditionalCalibration).not.toHaveBeenCalled();
      confirmSpy.mockRestore();
    });

    it("拟合失败时显示错误而不是静默", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      apiMocks.refreshConditionalCalibration.mockRejectedValue(new Error("401 未授权"));
      mountWith(bucketRows);
      await waitFor(() =>
        expect(screen.getByTestId("refresh-conditional")).toBeInTheDocument(),
      );
      fireEvent.change(screen.getByLabelText("引擎"), { target: { value: "basketball" } });
      fireEvent.change(screen.getByLabelText("赛事"), { target: { value: "nba" } });
      await userEvent.click(screen.getByTestId("refresh-conditional"));
      await waitFor(() => {
        expect(screen.getByTestId("conditional-error").textContent).toContain("401 未授权");
      });
      confirmSpy.mockRestore();
    });

    it("面板说明写清拟合不等于启用条件校准", async () => {
      mountWith(bucketRows);
      await waitFor(() =>
        expect(screen.getByTestId("conditional-calibration").textContent).toContain(
          "KERNEL_CONDITIONAL_CALIBRATION_ENABLED",
        ),
      );
    });
  });
});
