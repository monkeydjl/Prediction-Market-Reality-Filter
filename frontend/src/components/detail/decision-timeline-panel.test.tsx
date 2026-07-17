import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionTimelineResponse } from "@/lib/api";
import { eventsApi } from "@/lib/api";
import { DecisionTimelinePanel } from "./decision-timeline-panel";

vi.mock("@/lib/api", () => ({
  eventsApi: {
    decisionTimeline: vi.fn(),
  },
}));

const timeline: DecisionTimelineResponse = {
  event_id: "evt-1",
  count: 2,
  snapshots: [
    {
      snapshot_id: "snap-1",
      event_id: "evt-1",
      recorded_at: "2026-07-15T10:00:00Z",
      final_displayed_direction: "YES",
    },
    {
      snapshot_id: "snap-2",
      event_id: "evt-1",
      recorded_at: "2026-07-15T12:00:00Z",
      final_displayed_direction: "NO",
      final_downgrade_reason: "市场流动性不足",
      llm_degraded_mode: true,
    },
  ],
  diffs: [
    {
      direction_changed: true,
      prev_direction: "YES",
      current_direction: "NO",
      primary_change_driver: "market_move",
    },
  ],
};

describe("DecisionTimelinePanel", () => {
  beforeEach(() => {
    vi.mocked(eventsApi.decisionTimeline).mockReset();
  });

  it("加载期间显示加载中提示", async () => {
    vi.mocked(eventsApi.decisionTimeline).mockReturnValue(new Promise(() => {}));
    render(<DecisionTimelinePanel eventId="evt-1" />);

    expect(screen.getByText("加载中…")).toBeInTheDocument();
    expect(screen.getByText("决策变化时间线")).toBeInTheDocument();
    // API 调用在 setTimeout(0) 内，需异步等待
    await waitFor(() =>
      expect(eventsApi.decisionTimeline).toHaveBeenCalledWith("evt-1"),
    );
  });

  it("渲染快照列表并展示方向与变化驱动", async () => {
    vi.mocked(eventsApi.decisionTimeline).mockResolvedValue(timeline);
    render(<DecisionTimelinePanel eventId="evt-1" />);

    // 两个方向标签都应出现
    await waitFor(() => expect(screen.getByText("YES")).toBeInTheDocument());
    expect(screen.getByText("NO")).toBeInTheDocument();
    // 方向变化及驱动标签
    expect(screen.getByText("YES → NO")).toBeInTheDocument();
    expect(screen.getByText("概率显著变化")).toBeInTheDocument();
    // 降级原因与 LLM 降级模式
    expect(screen.getByText("降级原因：市场流动性不足")).toBeInTheDocument();
    expect(screen.getByText("LLM 降级模式")).toBeInTheDocument();
  });

  it("无快照时展示空状态提示", async () => {
    vi.mocked(eventsApi.decisionTimeline).mockResolvedValue({
      event_id: "evt-1",
      count: 0,
      snapshots: [],
      diffs: [],
    });
    render(<DecisionTimelinePanel eventId="evt-1" />);

    await waitFor(() =>
      expect(
        screen.getByText("暂无决策时间线数据。该事件可能在 DECISION_TIMELINE_ENABLED 关闭期间保存。"),
      ).toBeInTheDocument(),
    );
  });

  it("加载失败时展示错误信息", async () => {
    vi.mocked(eventsApi.decisionTimeline).mockRejectedValue(new Error("服务不可用"));
    render(<DecisionTimelinePanel eventId="evt-1" />);

    await waitFor(() => expect(screen.getByText("服务不可用")).toBeInTheDocument());
  });
});
