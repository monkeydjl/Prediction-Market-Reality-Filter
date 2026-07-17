import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MoversBoard } from "./movers-board";
import type { EventView } from "@/lib/adapt";

vi.mock("next/link", () => ({
  default: ({ href, children, className }: {
    href: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

function makeEvent(overrides: Partial<EventView> = {}): EventView {
  return {
    id: "evt-1",
    title: "事件标题 A",
    description: "",
    category: "us_presidential",
    currentProbability: 62,
    baselineProbability: 50,
    delta: 12,
    direction: "up",
    evidenceSupport: 0.7,
    priority: "medium",
    trackingStatus: "watching",
    trend: "up",
    valueScore: 0.5,
    ...overrides,
  };
}

describe("MoversBoard", () => {
  it("movers 为空时渲染空状态文案", () => {
    render(<MoversBoard movers={[]} sparklines={{}} />);
    expect(screen.getByText("概率异动榜")).toBeInTheDocument();
    expect(screen.getByText("暂无概率异动事件")).toBeInTheDocument();
    expect(
      screen.getByText("事件概率发生变化后，变动最大的事件将显示在这里"),
    ).toBeInTheDocument();
  });

  it("渲染最多 3 张异动卡片，每张包含标题和详情链接", () => {
    const movers = [
      makeEvent({ id: "e1", title: "事件一" }),
      makeEvent({ id: "e2", title: "事件二" }),
      makeEvent({ id: "e3", title: "事件三" }),
      makeEvent({ id: "e4", title: "事件四" }),
    ];
    const sparklines = {
      e1: [50, 55, 62],
      e2: [40, 50, 55],
      e3: [30, 35, 40],
    };
    render(<MoversBoard movers={movers} sparklines={sparklines} />);

    expect(screen.getByText("事件一")).toBeInTheDocument();
    expect(screen.getByText("事件二")).toBeInTheDocument();
    expect(screen.getByText("事件三")).toBeInTheDocument();
    // 第 4 个事件被截断，不渲染
    expect(screen.queryByText("事件四")).not.toBeInTheDocument();

    // 每张卡片渲染为指向 /events?id=... 的链接
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(3);
    expect(links[0].getAttribute("href")).toBe(
      `/events?id=${encodeURIComponent("e1")}`,
    );
  });

  it("渲染当前发生概率标签", () => {
    render(
      <MoversBoard
        movers={[makeEvent({ id: "e1", title: "事件一", currentProbability: 62 })]}
        sparklines={{ e1: [50, 55, 62] }}
      />,
    );
    expect(screen.getByText("当前发生概率")).toBeInTheDocument();
    // fmtPct(62, 0) → "62%"
    expect(screen.getByText("62%")).toBeInTheDocument();
  });
});
