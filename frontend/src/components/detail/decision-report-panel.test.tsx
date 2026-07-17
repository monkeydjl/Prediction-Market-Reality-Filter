import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionReport } from "@/lib/api";
import { eventsApi } from "@/lib/api";
import { DecisionReportPanel } from "./decision-report-panel";

// DecisionCard (rendered on success) uses next/link.
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", () => ({
  eventsApi: {
    decision: vi.fn(),
  },
}));

const report: DecisionReport = {
  event_id: "evt-1",
  event: { title: "Will the policy pass?", summary: "" },
  probability: { estimated: 60, baseline: 50, change: 10, direction: "up" },
  market_view: {
    market_probability: 50,
    platform: "manifold",
    liquidity: null,
    volume: null,
  },
  edge: { raw: 10, adjusted: 8, trust: 0.7 },
  diagnosis: {
    qualified: true,
    segment_n: 30,
    segment_skill: 0.6,
    liquidity_factor: 0.9,
    reason: "edge 经流动性调整后仍显著",
  },
  confidence: { level: "medium", score: 0.6, confidence: 0.6 },
  recommendation: { decision: "act", action: "押 YES", calibration_status: null },
  risk: { level: "medium", flags: [] },
  category: "politics",
  status: "open",
};

describe("DecisionReportPanel", () => {
  beforeEach(() => {
    vi.mocked(eventsApi.decision).mockReset();
  });

  it("加载期间显示加载中提示", async () => {
    // 让 promise 永远 pending，确保停留在 loading 态
    vi.mocked(eventsApi.decision).mockReturnValue(new Promise(() => {}));
    render(<DecisionReportPanel eventId="evt-1" />);

    expect(screen.getByText("加载中…")).toBeInTheDocument();
    expect(screen.getByText("决策分析")).toBeInTheDocument();
    // API 调用在 setTimeout(0) 内，需异步等待
    await waitFor(() =>
      expect(eventsApi.decision).toHaveBeenCalledWith("evt-1"),
    );
  });

  it("加载成功后渲染 DecisionCard 并展示事件标题", async () => {
    vi.mocked(eventsApi.decision).mockResolvedValue(report);
    render(<DecisionReportPanel eventId="evt-1" />);

    await waitFor(() =>
      expect(screen.getByText("Will the policy pass?")).toBeInTheDocument(),
    );
    // DecisionCard 显示的决策标签
    expect(screen.getByText("建议行动")).toBeInTheDocument();
  });

  it("加载失败时展示错误信息", async () => {
    vi.mocked(eventsApi.decision).mockRejectedValue(new Error("网络异常"));
    render(<DecisionReportPanel eventId="evt-1" />);

    await waitFor(() => expect(screen.getByText("网络异常")).toBeInTheDocument());
  });

  it("report 为 null 时展示缺省提示", async () => {
    vi.mocked(eventsApi.decision).mockResolvedValue(null as unknown as DecisionReport);
    render(<DecisionReportPanel eventId="evt-1" />);

    await waitFor(() =>
      expect(
        screen.getByText("暂无决策报告。该事件可能还没有冻结预测。"),
      ).toBeInTheDocument(),
    );
  });
});
