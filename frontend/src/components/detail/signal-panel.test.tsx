import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { EventRecord } from "@/lib/types";
import { SignalPanel } from "./signal-panel";

// 构造一个完整的 EventRecord 基线，便于在用例中按需覆盖。
const baseRecord: EventRecord = {
  event_id: "evt-1",
  event_title: "Will the policy pass?",
  event_summary: "",
  probability: { baseline: 50, estimated: 60, change: 10, direction: "up" },
  credibility: {
    score: 0.7,
    level: "medium",
    confidence: 0.5,
    news_quality: 0.6,
    evidence_strength: 0.7,
    source_count: 2,
  },
  impact: { score: 0.6, level: "medium", drivers: [] },
  risk: { level: "medium" },
  evidence: { direction: "rising", strength: 0.6, conflict: 0.2, freshness: 0.7, resolution_relevance: 0.5 },
  source: { type: "prediction_market" },
  value_score: 0.4,
  intelligence_report: {
    headline: "政策通过概率上升",
    why_it_matters: "影响短期市场情绪",
    probability_assessment: "模型估计 60%",
    recommended_action: "押 YES",
  },
  cross_validation: {
    model: "gpt-4o",
    probability: 58,
    agreement: "strong",
    divergence: 2,
  },
};

describe("SignalPanel", () => {
  it("渲染证据信号三个指标并展示方向标签", () => {
    render(<SignalPanel record={baseRecord} />);

    expect(screen.getByText("证据信号")).toBeInTheDocument();
    expect(screen.getByText("证据强度")).toBeInTheDocument();
    expect(screen.getByText("证据冲突")).toBeInTheDocument();
    expect(screen.getByText("信息新鲜度")).toBeInTheDocument();
    // DIRECTION_LABELS.rising = "上行"
    expect(screen.getByText("方向 上行")).toBeInTheDocument();
    // 百分比展示：strength=0.6 → "60%"
    expect(screen.getByText("60%")).toBeInTheDocument();
  });

  it("渲染交叉验证信息并将一致性映射为中文", () => {
    render(<SignalPanel record={baseRecord} />);

    expect(screen.getByText("交叉验证")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    // AGREEMENT_LABELS.strong = "高度一致"
    expect(screen.getByText("高度一致")).toBeInTheDocument();
    // probability=58 → "58%"
    expect(screen.getByText("58%")).toBeInTheDocument();
  });

  it("cross_validation 为 null 时展示缺省提示", () => {
    render(<SignalPanel record={{ ...baseRecord, cross_validation: null }} />);

    expect(screen.getByText("暂无交叉验证数据")).toBeInTheDocument();
    // 证据与情报解读仍应正常渲染
    expect(screen.getByText("证据强度")).toBeInTheDocument();
    expect(screen.getByText("政策通过概率上升")).toBeInTheDocument();
  });

  it("渲染情报解读的标题与为何重要", () => {
    render(<SignalPanel record={baseRecord} />);

    expect(screen.getByText("情报解读")).toBeInTheDocument();
    expect(screen.getByText("政策通过概率上升")).toBeInTheDocument();
    expect(screen.getByText("影响短期市场情绪")).toBeInTheDocument();
    expect(screen.getByText("模型估计 60%")).toBeInTheDocument();
  });
});
