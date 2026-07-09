import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EngineAutoTuneDashboard } from "./engine-auto-tune-dashboard";

vi.mock("@/lib/env", () => ({
  getWorldCupApiBase: () => "",
}));

vi.mock("@/lib/api", () => ({
  getOperatorApiKey: () => "",
}));

describe("EngineAutoTuneDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("/auto-tune/elo_odds")) {
        return Response.json({ status: "accepted", task_id: "task-1" });
      }
      if (url.includes("/auto-tune/status/task-1")) {
        return Response.json({ task: { status: "running" } });
      }
      return Response.json({ status: "ok" });
    }));
  });

  it("offers the integrated engine for auto tuning", () => {
    render(<EngineAutoTuneDashboard />);

    expect(screen.getByText("集成引擎")).toBeInTheDocument();
  });

  it("marks AI tuning as experimental and not official until verified", () => {
    render(<EngineAutoTuneDashboard />);

    expect(screen.getByText(/实验功能/)).toBeInTheDocument();
    expect(screen.getByText(/未验证结果不会影响正式概率/)).toBeInTheDocument();
  });

  it("does not poll auto-tune status while the tab is hidden", async () => {
    vi.useFakeTimers();
    try {
      render(<EngineAutoTuneDashboard />);

      await act(async () => {
        fireEvent.click(screen.getByTitle("基于 AI 优化反馈校准引擎参数（只有赛后验证后才会写入正式校准）"));
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/auto-tune/elo_odds?background=true"),
        expect.objectContaining({ method: "POST" }),
      );

      vi.mocked(fetch).mockClear();
      Object.defineProperty(document, "hidden", {
        configurable: true,
        value: true,
      });

      await act(async () => {
        vi.advanceTimersByTime(2_000);
        await Promise.resolve();
      });

      expect(fetch).not.toHaveBeenCalledWith(
        expect.stringContaining("/auto-tune/status/task-1"),
        expect.anything(),
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
